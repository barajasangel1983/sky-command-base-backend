import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from .database import SessionLocal
from .deps import ALGORITHM, SECRET_KEY, get_current_user, get_db, get_user_by_username
from .models import (
    AgentSession as AgentSessionModel,
    Prompt as PromptModel,
    TimelineEvent as TimelineEventModel,
    ToolCall as ToolCallModel,
)
from .schemas import (
    AgentSessionCreate,
    AgentSessionSchema,
    AgentSessionUpdate,
    PromptCreate,
    PromptSchema,
    TimelineEventCreate,
    TimelineEventSchema,
    ToolCallCreate,
    ToolCallSchema,
)

router = APIRouter(prefix="/monitor", tags=["monitor"])

# ---------------------------------------------------------------------------
# OpenClaw filesystem reader
# ---------------------------------------------------------------------------

_OPENCLAW_SESSIONS_JSON = os.path.expanduser("~/.openclaw/agents/main/sessions/sessions.json")


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block.get("text", "") for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content)


def _parse_jsonl(session_file: str) -> Tuple[Optional[str], Optional[str], int, int, list]:
    """Return (started_at_iso, model, tokens_in, tokens_out, message_events)."""
    started_at: Optional[str] = None
    model: Optional[str] = None
    tokens_in = 0
    tokens_out = 0
    message_events: list = []
    try:
        with open(session_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                t = ev.get("type", "")
                if t == "session" and started_at is None:
                    started_at = ev.get("timestamp")
                if t == "custom" and ev.get("customType") == "model-snapshot":
                    data = ev.get("data", {})
                    provider = data.get("provider", "")
                    model_id = data.get("modelId", "")
                    if provider and model_id:
                        model = f"{provider}/{model_id}"
                if t == "message":
                    msg = ev.get("message", {})
                    usage = msg.get("usage", {})
                    tokens_in += usage.get("input", 0) + usage.get("cacheRead", 0)
                    tokens_out += usage.get("output", 0)
                    message_events.append(ev)
    except Exception:
        pass
    return started_at, model, tokens_in, tokens_out, message_events


def _openclaw_index() -> dict:
    """Return a mapping of sessionId -> session entry dict."""
    if not os.path.exists(_OPENCLAW_SESSIONS_JSON):
        return {}
    try:
        with open(_OPENCLAW_SESSIONS_JSON) as f:
            data = json.load(f)
        index: dict = {}
        for val in data.values():
            if isinstance(val, dict) and val.get("sessionId"):
                index[val["sessionId"]] = val
        return index
    except Exception:
        return {}


def _read_openclaw_sessions() -> List[AgentSessionSchema]:
    if not os.path.exists(_OPENCLAW_SESSIONS_JSON):
        return []
    try:
        with open(_OPENCLAW_SESSIONS_JSON) as f:
            sessions_data = json.load(f)
    except Exception:
        return []

    result: List[AgentSessionSchema] = []
    for key, val in sessions_data.items():
        if not isinstance(val, dict):
            continue
        session_id = val.get("sessionId")
        if not session_id:
            continue

        channel = val.get("channel", "openclaw")
        group_channel = val.get("groupChannel", "")
        display_name = val.get("displayName", key)
        updated_at_ms = val.get("updatedAt", 0)
        session_file = val.get("sessionFile", "")

        channel_label = f"{channel}{group_channel}" if group_channel else channel

        started_at: Optional[str] = None
        model = "openai-codex/gpt-5.1"
        tokens_in = 0
        tokens_out = 0

        if session_file and os.path.exists(session_file):
            started_at, parsed_model, tokens_in, tokens_out, _ = _parse_jsonl(session_file)
            if parsed_model:
                model = parsed_model

        if not started_at:
            if updated_at_ms:
                dt = datetime.fromtimestamp(updated_at_ms / 1000, tz=timezone.utc)
                started_at = dt.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
            else:
                started_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S") + "Z"

        result.append(AgentSessionSchema(
            id=session_id,
            startedAt=started_at,
            endedAt=None,
            model=model,
            channel=channel_label,
            tokensIn=tokens_in,
            tokensOut=tokens_out,
            status="running",
            summary=display_name,
        ))

    return result


def _get_claude_tokens(session_id: str, cwd: str) -> Tuple[int, int]:
    """Read token counts from Claude Code's own JSONL for a session."""
    project_path = cwd.replace("/", "-").replace("_", "-")  # /home/foo_bar → -home-foo-bar
    jsonl = os.path.expanduser(f"~/.claude/projects/{project_path}/{session_id}.jsonl")
    if not os.path.exists(jsonl):
        return 0, 0
    tokens_in = 0
    tokens_out = 0
    try:
        with open(jsonl) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if ev.get("type") == "assistant":
                    usage = ev.get("message", {}).get("usage", {})
                    tokens_in += (
                        usage.get("input_tokens", 0)
                        + usage.get("cache_read_input_tokens", 0)
                        + usage.get("cache_creation_input_tokens", 0)
                    )
                    tokens_out += usage.get("output_tokens", 0)
    except Exception:
        pass
    return tokens_in, tokens_out


def _openclaw_timeline(session_id: str, index: dict) -> List[TimelineEventSchema]:
    entry = index.get(session_id)
    if not entry:
        return []
    session_file = entry.get("sessionFile", "")
    if not session_file or not os.path.exists(session_file):
        return []
    _, _, _, _, events = _parse_jsonl(session_file)
    result: List[TimelineEventSchema] = []
    for ev in events:
        msg = ev.get("message", {})
        role = msg.get("role", "")
        if role not in ("user", "assistant"):
            continue
        event_type = "prompt" if role == "user" else "response"
        text = _extract_text(msg.get("content", ""))
        ts = ev.get("timestamp", datetime.utcnow().isoformat() + "Z")
        result.append(TimelineEventSchema(
            id=ev.get("id", str(uuid4())),
            sessionId=session_id,
            type=event_type,
            timestamp=ts,
            content=text[:1000] + ("…" if len(text) > 1000 else ""),
            metadata=None,
        ))
    return result


def _openclaw_prompts(session_id: str, index: dict) -> List[PromptSchema]:
    entry = index.get(session_id)
    if not entry:
        return []
    session_file = entry.get("sessionFile", "")
    if not session_file or not os.path.exists(session_file):
        return []
    _, _, _, _, events = _parse_jsonl(session_file)
    result: List[PromptSchema] = []
    for ev in events:
        msg = ev.get("message", {})
        role = msg.get("role", "")
        if role not in ("user", "assistant"):
            continue
        text = _extract_text(msg.get("content", ""))
        usage = msg.get("usage", {})
        tokens = usage.get("output", 0) if role == "assistant" else usage.get("input", 0)
        ts = ev.get("timestamp", datetime.utcnow().isoformat() + "Z")
        result.append(PromptSchema(
            id=ev.get("id", str(uuid4())),
            sessionId=session_id,
            role=role,
            content=text[:4000],
            timestamp=ts,
            tokens=tokens,
        ))
    return result

# ---------------------------------------------------------------------------
# Token stats helpers
# ---------------------------------------------------------------------------

_BUDGET_FILE = os.path.expanduser("~/.sky_command_budget.json")

_RATE_IN = float(os.getenv("TOKEN_RATE_IN", "0.003"))   # per 1K tokens
_RATE_OUT = float(os.getenv("TOKEN_RATE_OUT", "0.015"))  # per 1K tokens


def _model_rates(model: Optional[str]) -> Tuple[float, float]:
    """Return (rate_in, rate_out) for a model. Local/LM Studio models get 0."""
    if model and any(k in model.lower() for k in ("local", "lmstudio")):
        return 0.0, 0.0
    return _RATE_IN, _RATE_OUT


def _session_cost(tokens_in: int, tokens_out: int, model: Optional[str]) -> float:
    r_in, r_out = _model_rates(model)
    return (tokens_in / 1000) * r_in + (tokens_out / 1000) * r_out


def _today_start_utc() -> datetime:
    now = datetime.now(tz=timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _all_sessions_as_dicts(db: "Session") -> List[Dict]:
    """Return all sessions (DB + OpenClaw) as plain dicts."""
    db_sessions = (
        db.query(AgentSessionModel)
        .order_by(AgentSessionModel.started_at.desc())
        .all()
    )
    db_ids = {s.id for s in db_sessions}

    result: List[Dict] = []

    for s in db_sessions:
        tokens_in = s.tokens_in or 0
        tokens_out = s.tokens_out or 0
        if tokens_in == 0 and tokens_out == 0:
            tokens_in, tokens_out = _get_claude_tokens(s.id, s.channel or "")
        started_at = _dt(s.started_at) or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S") + "Z"
        result.append({
            "sessionId": s.id,
            "model": s.model,
            "channel": s.channel or "",
            "tokensIn": tokens_in,
            "tokensOut": tokens_out,
            "timestamp": started_at,
            "status": s.status,
        })

    for oc in _read_openclaw_sessions():
        if oc.id not in db_ids:
            result.append({
                "sessionId": oc.id,
                "model": oc.model,
                "channel": oc.channel or "",
                "tokensIn": oc.tokensIn,
                "tokensOut": oc.tokensOut,
                "timestamp": oc.startedAt or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S") + "Z",
                "status": oc.status,
            })

    return result


# ---------------------------------------------------------------------------
# Token stats endpoint
# ---------------------------------------------------------------------------

@router.get("/token-stats")
async def get_token_stats(
    db: "Session" = Depends(get_db),
    _user=Depends(get_current_user),
) -> Dict:
    sessions = _all_sessions_as_dicts(db)
    today_start = _today_start_utc()

    today_sessions = []
    for s in sessions:
        ts_str = s["timestamp"].replace("Z", "+00:00")
        try:
            ts = datetime.fromisoformat(ts_str)
        except Exception:
            ts = datetime.now(tz=timezone.utc)
        if ts >= today_start:
            today_sessions.append(s)

    total_cost_today = sum(_session_cost(s["tokensIn"], s["tokensOut"], s["model"]) for s in today_sessions)
    tokens_in_today = sum(s["tokensIn"] for s in today_sessions)
    tokens_out_today = sum(s["tokensOut"] for s in today_sessions)
    active_sessions = sum(1 for s in sessions if s.get("status") == "running")

    # Top sessions by cost, all time, limited to 10
    top_sessions = sorted(
        [
            {
                "sessionId": s["sessionId"],
                "model": s["model"],
                "channel": s["channel"],
                "tokensIn": s["tokensIn"],
                "tokensOut": s["tokensOut"],
                "cost": round(_session_cost(s["tokensIn"], s["tokensOut"], s["model"]), 4),
                "timestamp": s["timestamp"],
            }
            for s in sessions
        ],
        key=lambda x: x["cost"],
        reverse=True,
    )[:10]

    # Model breakdown
    model_map: Dict[str, Dict] = {}
    for s in sessions:
        m = s["model"] or "unknown"
        if m not in model_map:
            model_map[m] = {"model": m, "sessions": 0, "tokensIn": 0, "tokensOut": 0, "totalTokens": 0, "totalCost": 0.0}
        model_map[m]["sessions"] += 1
        model_map[m]["tokensIn"] += s["tokensIn"]
        model_map[m]["tokensOut"] += s["tokensOut"]
        model_map[m]["totalTokens"] += s["tokensIn"] + s["tokensOut"]
        model_map[m]["totalCost"] = round(
            model_map[m]["totalCost"] + _session_cost(s["tokensIn"], s["tokensOut"], m), 4
        )

    return {
        "todaySnapshot": {
            "totalCost": round(total_cost_today, 4),
            "tokensIn": tokens_in_today,
            "tokensOut": tokens_out_today,
            "activeSessions": active_sessions,
        },
        "topSessions": top_sessions,
        "modelBreakdown": list(model_map.values()),
        "ratesInfo": {"rateIn": _RATE_IN, "rateOut": _RATE_OUT},
    }


# ---------------------------------------------------------------------------
# Budget endpoints
# ---------------------------------------------------------------------------

@router.get("/budget")
async def get_budget(_user=Depends(get_current_user)) -> Dict:
    if os.path.exists(_BUDGET_FILE):
        try:
            with open(_BUDGET_FILE) as f:
                data = json.load(f)
            return {"dailyBudget": float(data.get("dailyBudget", 2.00))}
        except Exception:
            pass
    return {"dailyBudget": 2.00}


@router.put("/budget")
async def set_budget(body: Dict, _user=Depends(get_current_user)) -> Dict:
    budget = float(body.get("dailyBudget", 2.00))
    with open(_BUDGET_FILE, "w") as f:
        json.dump({"dailyBudget": budget}, f)
    return {"dailyBudget": budget}


# ---------------------------------------------------------------------------
# WebSocket broadcast hub
# ---------------------------------------------------------------------------

_ws_queues: List[asyncio.Queue] = []


async def _broadcast(event: dict) -> None:
    dead = []
    for q in _ws_queues:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        if q in _ws_queues:
            _ws_queues.remove(q)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dt(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + "Z"


def _session_schema(s: AgentSessionModel) -> AgentSessionSchema:
    return AgentSessionSchema(
        id=s.id,
        startedAt=_dt(s.started_at),  # type: ignore[arg-type]
        endedAt=_dt(s.ended_at),
        model=s.model,
        channel=s.channel,
        tokensIn=s.tokens_in,
        tokensOut=s.tokens_out,
        status=s.status,  # type: ignore[arg-type]
        summary=s.summary,
    )


# ---------------------------------------------------------------------------
# Session endpoints
# ---------------------------------------------------------------------------

@router.get("/sessions", response_model=List[AgentSessionSchema])
async def list_sessions(
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    db_sessions = (
        db.query(AgentSessionModel)
        .order_by(AgentSessionModel.started_at.desc())
        .all()
    )
    db_ids = {s.id for s in db_sessions}
    result = []
    for s in db_sessions:
        schema = _session_schema(s)
        if schema.tokensIn == 0 and schema.tokensOut == 0:
            tok_in, tok_out = _get_claude_tokens(s.id, s.channel)
            schema.tokensIn = tok_in
            schema.tokensOut = tok_out
        result.append(schema)

    for oc_session in _read_openclaw_sessions():
        if oc_session.id not in db_ids:
            result.append(oc_session)

    result.sort(key=lambda s: s.startedAt, reverse=True)
    return result


@router.post("/sessions", response_model=AgentSessionSchema, status_code=201)
async def create_session(
    payload: AgentSessionCreate,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    session = AgentSessionModel(
        id=payload.id or str(uuid4()),
        model=payload.model,
        channel=payload.channel,
        tokens_in=payload.tokens_in,
        tokens_out=payload.tokens_out,
        status=payload.status,
        summary=payload.summary,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    schema = _session_schema(session)
    await _broadcast({"type": "session_created", "payload": schema.model_dump()})
    return schema


@router.get("/sessions/{session_id}", response_model=AgentSessionSchema)
async def get_session(
    session_id: str,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    s = db.query(AgentSessionModel).filter(AgentSessionModel.id == session_id).first()
    if s:
        return _session_schema(s)
    # Fall back to OpenClaw index
    index = _openclaw_index()
    if session_id in index:
        for oc_session in _read_openclaw_sessions():
            if oc_session.id == session_id:
                return oc_session
    raise HTTPException(status_code=404, detail="Session not found")


@router.patch("/sessions/{session_id}", response_model=AgentSessionSchema)
async def update_session(
    session_id: str,
    payload: AgentSessionUpdate,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    s = db.query(AgentSessionModel).filter(AgentSessionModel.id == session_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    if payload.status is not None:
        s.status = payload.status
        if payload.status in ("completed", "error") and s.ended_at is None:
            s.ended_at = datetime.utcnow()
    if payload.tokens_in is not None:
        s.tokens_in = payload.tokens_in
    if payload.tokens_out is not None:
        s.tokens_out = payload.tokens_out
    if payload.summary is not None:
        s.summary = payload.summary
    db.commit()
    db.refresh(s)
    schema = _session_schema(s)
    await _broadcast({"type": "session_update", "payload": schema.model_dump()})
    return schema


# ---------------------------------------------------------------------------
# Timeline endpoints
# ---------------------------------------------------------------------------

@router.get("/sessions/{session_id}/timeline", response_model=List[TimelineEventSchema])
async def get_timeline(
    session_id: str,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    # Check OpenClaw first
    index = _openclaw_index()
    if session_id in index:
        return _openclaw_timeline(session_id, index)

    events = (
        db.query(TimelineEventModel)
        .filter(TimelineEventModel.session_id == session_id)
        .order_by(TimelineEventModel.timestamp)
        .all()
    )
    return [
        TimelineEventSchema(
            id=e.id,
            sessionId=e.session_id,
            type=e.type,
            timestamp=_dt(e.timestamp),  # type: ignore[arg-type]
            content=e.content,
            metadata=json.loads(e.metadata_json) if e.metadata_json else None,
        )
        for e in events
    ]


@router.post("/sessions/{session_id}/events", response_model=TimelineEventSchema, status_code=201)
async def add_event(
    session_id: str,
    payload: TimelineEventCreate,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    if not db.query(AgentSessionModel).filter(AgentSessionModel.id == session_id).first():
        raise HTTPException(status_code=404, detail="Session not found")
    event = TimelineEventModel(
        id=str(uuid4()),
        session_id=session_id,
        type=payload.type,
        content=payload.content,
        metadata_json=json.dumps(payload.metadata) if payload.metadata else None,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    schema = TimelineEventSchema(
        id=event.id,
        sessionId=event.session_id,
        type=event.type,
        timestamp=_dt(event.timestamp),  # type: ignore[arg-type]
        content=event.content,
        metadata=payload.metadata,
    )
    await _broadcast({"type": "timeline_event", "payload": {"sessionId": session_id, **schema.model_dump()}})
    return schema


# ---------------------------------------------------------------------------
# Tool call endpoints
# ---------------------------------------------------------------------------

@router.get("/sessions/{session_id}/tool-calls", response_model=List[ToolCallSchema])
async def get_tool_calls(
    session_id: str,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    # OpenClaw sessions store tool use inside message content — no separate tool-calls table
    index = _openclaw_index()
    if session_id in index:
        return []

    tcs = (
        db.query(ToolCallModel)
        .filter(ToolCallModel.session_id == session_id)
        .order_by(ToolCallModel.started_at)
        .all()
    )
    return [
        ToolCallSchema(
            id=tc.id,
            sessionId=tc.session_id,
            name=tc.name,
            input=tc.input,
            output=tc.output,
            startedAt=_dt(tc.started_at),  # type: ignore[arg-type]
            endedAt=_dt(tc.ended_at or tc.started_at),  # type: ignore[arg-type]
            status=tc.status,  # type: ignore[arg-type]
        )
        for tc in tcs
    ]


@router.post("/sessions/{session_id}/tool-calls", response_model=ToolCallSchema, status_code=201)
async def add_tool_call(
    session_id: str,
    payload: ToolCallCreate,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    if not db.query(AgentSessionModel).filter(AgentSessionModel.id == session_id).first():
        raise HTTPException(status_code=404, detail="Session not found")
    now = datetime.utcnow()
    tc = ToolCallModel(
        id=str(uuid4()),
        session_id=session_id,
        name=payload.name,
        input=payload.input,
        output=payload.output,
        started_at=now,
        ended_at=now,
        status=payload.status,
    )
    db.add(tc)
    db.commit()
    db.refresh(tc)
    return ToolCallSchema(
        id=tc.id,
        sessionId=tc.session_id,
        name=tc.name,
        input=tc.input,
        output=tc.output,
        startedAt=_dt(tc.started_at),  # type: ignore[arg-type]
        endedAt=_dt(tc.ended_at),  # type: ignore[arg-type]
        status=tc.status,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Prompt endpoints
# ---------------------------------------------------------------------------

@router.get("/sessions/{session_id}/prompts", response_model=List[PromptSchema])
async def get_prompts(
    session_id: str,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    # Check OpenClaw first
    index = _openclaw_index()
    if session_id in index:
        return _openclaw_prompts(session_id, index)

    ps = (
        db.query(PromptModel)
        .filter(PromptModel.session_id == session_id)
        .order_by(PromptModel.timestamp)
        .all()
    )
    return [
        PromptSchema(
            id=p.id,
            sessionId=p.session_id,
            role=p.role,  # type: ignore[arg-type]
            content=p.content,
            timestamp=_dt(p.timestamp),  # type: ignore[arg-type]
            tokens=p.tokens,
        )
        for p in ps
    ]


@router.post("/sessions/{session_id}/prompts", response_model=PromptSchema, status_code=201)
async def add_prompt(
    session_id: str,
    payload: PromptCreate,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    if not db.query(AgentSessionModel).filter(AgentSessionModel.id == session_id).first():
        raise HTTPException(status_code=404, detail="Session not found")
    p = PromptModel(
        id=str(uuid4()),
        session_id=session_id,
        role=payload.role,
        content=payload.content,
        tokens=payload.tokens,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return PromptSchema(
        id=p.id,
        sessionId=p.session_id,
        role=p.role,  # type: ignore[arg-type]
        content=p.content,
        timestamp=_dt(p.timestamp),  # type: ignore[arg-type]
        tokens=p.tokens,
    )


# ---------------------------------------------------------------------------
# Hook endpoint (called by Claude Code hooks — uses static API key auth)
# ---------------------------------------------------------------------------

@router.post("/hook", status_code=200)
async def handle_hook(
    payload: dict[str, Any],
    x_monitor_key: str | None = Header(None),
    db: Session = Depends(get_db),
):
    api_key = os.getenv("MONITOR_API_KEY", "")
    if not api_key or x_monitor_key != api_key:
        raise HTTPException(status_code=401, detail="Invalid monitor API key")

    event = payload.get("hook_event_name", "")
    session_id = payload.get("session_id", "")
    if not session_id:
        return {"ok": True}

    if event == "SessionStart":
        existing = db.query(AgentSessionModel).filter(AgentSessionModel.id == session_id).first()
        if not existing:
            s = AgentSessionModel(
                id=session_id,
                model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"),
                channel=payload.get("cwd", ""),
                status="running",
            )
            db.add(s)
            db.commit()
            db.refresh(s)
            await _broadcast({"type": "session_created", "payload": _session_schema(s).model_dump()})

    elif event == "SessionEnd":
        s = db.query(AgentSessionModel).filter(AgentSessionModel.id == session_id).first()
        if s and s.status == "running":
            s.status = "completed"
            s.ended_at = datetime.utcnow()
            db.commit()
            db.refresh(s)
            await _broadcast({"type": "session_update", "payload": _session_schema(s).model_dump()})

    elif event == "UserPromptSubmit":
        s = db.query(AgentSessionModel).filter(AgentSessionModel.id == session_id).first()
        if s:
            content = payload.get("prompt", "")[:4000]
            ev = TimelineEventModel(
                id=str(uuid4()),
                session_id=session_id,
                type="prompt",
                content=content,
            )
            db.add(ev)
            db.commit()

    elif event == "PostToolUse":
        s = db.query(AgentSessionModel).filter(AgentSessionModel.id == session_id).first()
        if s:
            now = datetime.utcnow()
            tool_input = payload.get("tool_input", {})
            tool_output = payload.get("tool_output", "")
            tc = ToolCallModel(
                id=str(uuid4()),
                session_id=session_id,
                name=payload.get("tool_name", ""),
                input=json.dumps(tool_input) if isinstance(tool_input, dict) else str(tool_input)[:2000],
                output=str(tool_output)[:2000],
                started_at=now,
                ended_at=now,
                status="success",
            )
            db.add(tc)
            db.commit()

    elif event == "Stop":
        s = db.query(AgentSessionModel).filter(AgentSessionModel.id == session_id).first()
        if s:
            ev = TimelineEventModel(
                id=str(uuid4()),
                session_id=session_id,
                type="response",
                content=str(payload.get("stop_reason", "response complete")),
            )
            db.add(ev)
            db.commit()

    return {"ok": True}


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@router.websocket("/ws")
async def websocket_monitor(websocket: WebSocket, token: str = Query("")):
    db = SessionLocal()
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str | None = payload.get("sub")
        if not username:
            await websocket.close(code=1008)
            return
        user = get_user_by_username(db, username)
        if not user:
            await websocket.close(code=1008)
            return
    except JWTError:
        await websocket.close(code=1008)
        return
    finally:
        db.close()

    await websocket.accept()
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    _ws_queues.append(queue)
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=20)
                await websocket.send_json(event)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        if queue in _ws_queues:
            _ws_queues.remove(queue)


