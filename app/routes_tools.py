import json
import os
import subprocess
import time
from datetime import datetime, timezone

import psutil
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .database import SessionLocal
from .deps import get_current_user, get_db
from .models import (
    AgentSession,
    KanbanCard,
    Prompt,
    ResearchIdeaDrop,
    ResearchModule,
    ResearchNextAction,
    ResearchReport,
    TimelineEvent,
    ToolCall,
)

router = APIRouter(prefix="/tools", tags=["tools"])


# ---------------------------------------------------------------------------
# System health
# ---------------------------------------------------------------------------

@router.get("/health")
async def system_health(_user=Depends(get_current_user)):
    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    uptime_secs = time.time() - psutil.boot_time()
    d = int(uptime_secs // 86400)
    h = int((uptime_secs % 86400) // 3600)
    m = int((uptime_secs % 3600) // 60)

    return {
        "cpu": round(cpu, 1),
        "memory": round(mem.percent, 1),
        "memoryUsedMb": mem.used // (1024 * 1024),
        "memoryTotalMb": mem.total // (1024 * 1024),
        "disk": round(disk.percent, 1),
        "diskUsedGb": disk.used // (1024 ** 3),
        "diskTotalGb": disk.total // (1024 ** 3),
        "uptime": f"{d}d {h}h {m}m",
    }


# ---------------------------------------------------------------------------
# Data export
# ---------------------------------------------------------------------------

def _dt(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + "Z"


@router.get("/export")
async def export_data(
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    payload = {
        "exportedAt": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z",
        "kanbanCards": [
            {
                "id": c.id, "title": c.title, "description": c.description,
                "column": c.column, "priority": c.priority, "createdAt": _dt(c.created_at),
            }
            for c in db.query(KanbanCard).all()
        ],
        "agentSessions": [
            {
                "id": s.id, "model": s.model, "channel": s.channel,
                "tokensIn": s.tokens_in, "tokensOut": s.tokens_out,
                "status": s.status, "summary": s.summary,
                "startedAt": _dt(s.started_at), "endedAt": _dt(s.ended_at),
            }
            for s in db.query(AgentSession).all()
        ],
        "timelineEvents": [
            {
                "id": e.id, "sessionId": e.session_id, "type": e.type,
                "content": e.content, "timestamp": _dt(e.timestamp),
            }
            for e in db.query(TimelineEvent).all()
        ],
        "toolCalls": [
            {
                "id": t.id, "sessionId": t.session_id, "name": t.name,
                "input": t.input, "output": t.output, "status": t.status,
                "startedAt": _dt(t.started_at), "endedAt": _dt(t.ended_at),
            }
            for t in db.query(ToolCall).all()
        ],
        "prompts": [
            {
                "id": p.id, "sessionId": p.session_id, "role": p.role,
                "content": p.content, "tokens": p.tokens, "timestamp": _dt(p.timestamp),
            }
            for p in db.query(Prompt).all()
        ],
        "research": [
            {
                "slug": m.slug, "name": m.name, "icon": m.icon, "topic": m.topic,
                "setBy": m.set_by, "enabled": m.enabled,
                "reports": [
                    {
                        "id": r.id, "title": r.title, "source": r.source, "date": r.date,
                        "score": r.score, "link": r.link, "topic": r.topic,
                    }
                    for r in db.query(ResearchReport).filter(ResearchReport.module_slug == m.slug).all()
                ],
                "ideaDrops": [
                    {
                        "id": i.id, "title": i.title, "hook": i.hook, "category": i.category,
                        "priority": i.priority, "createdBy": i.created_by,
                    }
                    for i in db.query(ResearchIdeaDrop).filter(ResearchIdeaDrop.module_slug == m.slug).all()
                ],
                "nextActions": [
                    {
                        "id": a.id, "label": a.label, "actionType": a.action_type,
                        "completed": a.completed,
                    }
                    for a in db.query(ResearchNextAction).filter(ResearchNextAction.module_slug == m.slug).all()
                ],
            }
            for m in db.query(ResearchModule).order_by(ResearchModule.sort_order).all()
        ],
    }

    filename = f"sky-command-export-{datetime.now(tz=timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    return Response(
        content=json.dumps(payload, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# OpenClaw status
# ---------------------------------------------------------------------------

_OPENCLAW_CFG = os.path.expanduser("~/.openclaw/openclaw.json")
_OPENCLAW_SESSIONS = os.path.expanduser("~/.openclaw/agents/main/sessions/sessions.json")
_OPENCLAW_CRON = os.path.expanduser("~/.openclaw/cron/jobs.json")


@router.get("/openclaw-status")
async def openclaw_status(_user=Depends(get_current_user)):
    # Config
    cfg: dict = {}
    if os.path.exists(_OPENCLAW_CFG):
        try:
            with open(_OPENCLAW_CFG) as f:
                cfg = json.load(f)
        except Exception:
            pass

    gateway = cfg.get("gateway", {})
    port = gateway.get("port", 18789)
    version = cfg.get("meta", {}).get("lastTouchedVersion", "unknown")

    # Channels
    channels_cfg = cfg.get("channels", {})
    channels = [
        {"name": name, "enabled": ch.get("enabled", True)}
        for name, ch in channels_cfg.items()
    ]

    # Primary model
    agent_defaults = cfg.get("agents", {}).get("defaults", {})
    model_cfg = agent_defaults.get("model", {})
    primary_model = model_cfg.get("primary", "unknown")
    fallback_models = model_cfg.get("fallbacks", [])

    # Cron jobs
    cron_jobs: list = []
    if os.path.exists(_OPENCLAW_CRON):
        try:
            with open(_OPENCLAW_CRON) as f:
                cron_jobs = json.load(f).get("jobs", [])
        except Exception:
            pass

    # Sessions
    sessions_data: dict = {}
    if os.path.exists(_OPENCLAW_SESSIONS):
        try:
            with open(_OPENCLAW_SESSIONS) as f:
                sessions_data = json.load(f)
        except Exception:
            pass

    total_sessions = len(sessions_data)

    recent_sessions = sorted(
        [
            {
                "sessionId": v.get("sessionId", k),
                "channel": (v.get("channel") or "unknown") + (
                    f"/{v['groupChannel']}" if v.get("groupChannel") else ""
                ),
                "displayName": v.get("displayName") or v.get("sessionId", k),
                "updatedAt": datetime.fromtimestamp(
                    v["updatedAt"] / 1000, tz=timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%S") + "Z"
                if v.get("updatedAt") else None,
            }
            for k, v in sessions_data.items()
            if isinstance(v, dict)
        ],
        key=lambda x: x.get("updatedAt") or "",
        reverse=True,
    )[:8]

    # Is gateway process running?
    gateway_running = any(
        "openclaw-gateway" in (p.name() if hasattr(p, "name") else "")
        for p in psutil.process_iter(["name"])
    )

    return {
        "gateway": {
            "port": port,
            "version": version,
            "running": gateway_running,
            "uiUrl": f"http://localhost:{port}",
        },
        "channels": channels,
        "primaryModel": primary_model,
        "fallbackModels": fallback_models,
        "cronJobs": len(cron_jobs),
        "totalSessions": total_sessions,
        "recentSessions": recent_sessions,
    }


# ---------------------------------------------------------------------------
# Log download
# ---------------------------------------------------------------------------

@router.get("/logs")
async def download_logs(_user=Depends(get_current_user)):
    try:
        result = subprocess.run(
            ["journalctl", "-u", "sky-command-backend", "--no-pager", "-n", "2000",
             "--output", "short-iso"],
            capture_output=True, text=True, timeout=15,
        )
        content = result.stdout or result.stderr or "No log output available."
    except FileNotFoundError:
        content = "journalctl not available on this system."
    except subprocess.TimeoutExpired:
        content = "Log retrieval timed out."

    filename = f"sky-command-logs-{datetime.now(tz=timezone.utc).strftime('%Y%m%d-%H%M%S')}.log"
    return Response(
        content=content,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# OpenClaw agent cards
# ---------------------------------------------------------------------------

_OPENCLAW_AGENTS_DIR = os.path.expanduser("~/.openclaw/agents")
_OPENCLAW_WORKSPACE = os.path.expanduser("~/.openclaw/workspace")
_AGENT_SLOTS = ["main", "research", "discord", "whatsapp"]
_AGENT_DISPLAY_NAMES: dict[str, str] = {"main": "Frinko"}


@router.get("/openclaw-agents")
async def openclaw_agents(_user=Depends(get_current_user)):
    existing: list[str] = []
    if os.path.isdir(_OPENCLAW_AGENTS_DIR):
        existing = [
            d for d in os.listdir(_OPENCLAW_AGENTS_DIR)
            if os.path.isdir(os.path.join(_OPENCLAW_AGENTS_DIR, d))
        ]

    # Top-level .md files in the shared workspace
    md_files: list[str] = []
    if os.path.isdir(_OPENCLAW_WORKSPACE):
        md_files = sorted(
            f for f in os.listdir(_OPENCLAW_WORKSPACE)
            if f.endswith(".md") and os.path.isfile(os.path.join(_OPENCLAW_WORKSPACE, f))
        )

    slots = list(_AGENT_SLOTS)
    for name in existing:
        if name not in slots:
            slots.append(name)
    slots = slots[:4]

    agents = [
        {
            "name": name,
            "displayName": _AGENT_DISPLAY_NAMES.get(name, name.capitalize()),
            "exists": name in existing,
            "mdFiles": md_files if name in existing else [],
        }
        for name in slots
    ]
    return {"agents": agents}


@router.get("/openclaw-agent-file")
async def get_agent_file(
    path: str = Query(..., description="Filename relative to workspace root"),
    _user=Depends(get_current_user),
):
    workspace_real = os.path.realpath(_OPENCLAW_WORKSPACE)
    full_path = os.path.realpath(os.path.join(_OPENCLAW_WORKSPACE, path))
    if not full_path.startswith(workspace_real + os.sep) and full_path != workspace_real:
        raise HTTPException(status_code=403, detail="Access denied")
    if not os.path.isfile(full_path):
        return {"content": ""}
    with open(full_path, encoding="utf-8") as f:
        return {"content": f.read()}


class AgentFileBody(BaseModel):
    path: str
    content: str


@router.put("/openclaw-agent-file")
async def put_agent_file(body: AgentFileBody, _user=Depends(get_current_user)):
    workspace_real = os.path.realpath(_OPENCLAW_WORKSPACE)
    full_path = os.path.realpath(os.path.join(_OPENCLAW_WORKSPACE, body.path))
    if not full_path.startswith(workspace_real + os.sep) and full_path != workspace_real:
        raise HTTPException(status_code=403, detail="Access denied")
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(body.content)
    return {"ok": True}


# ---------------------------------------------------------------------------
# OpenClaw cron
# ---------------------------------------------------------------------------

class CronBody(BaseModel):
    raw: str


@router.get("/openclaw-cron")
async def get_openclaw_cron(_user=Depends(get_current_user)):
    raw = '{"version": 1, "jobs": []}'
    jobs: list = []
    version: int = 1
    if os.path.exists(_OPENCLAW_CRON):
        try:
            with open(_OPENCLAW_CRON, encoding="utf-8") as f:
                raw = f.read()
            data = json.loads(raw)
            jobs = data.get("jobs", [])
            version = data.get("version", 1)
        except Exception:
            pass
    return {"version": version, "jobs": jobs, "raw": raw}


@router.put("/openclaw-cron")
async def put_openclaw_cron(body: CronBody, _user=Depends(get_current_user)):
    try:
        json.loads(body.raw)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")
    with open(_OPENCLAW_CRON, "w", encoding="utf-8") as f:
        f.write(body.raw)
    return {"ok": True}
