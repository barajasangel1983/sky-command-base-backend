import asyncio
import json
from datetime import datetime
from typing import List
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
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
    sessions = (
        db.query(AgentSessionModel)
        .order_by(AgentSessionModel.started_at.desc())
        .all()
    )
    return [_session_schema(s) for s in sessions]


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
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    return _session_schema(s)


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


