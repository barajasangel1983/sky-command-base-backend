import json
import os
from datetime import datetime
from typing import List, Optional
from uuid import uuid4

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .deps import get_current_admin, get_current_user, get_db
from .models import (
    AIIdeaDrop as AIIdeaDropModel,
    AINextAction as AINextActionModel,
    AIResearchConfig as AIResearchConfigModel,
    AIResearchReport as AIResearchReportModel,
)
from .schemas import (
    AIDbHealthSchema,
    AIDigestPayload,
    AIIdeaDropCreate,
    AIIdeaDropSchema,
    AIIdeaDropUpdate,
    AINextActionCreate,
    AINextActionSchema,
    AINextActionUpdate,
    AIResearchConfigSchema,
    AIResearchConfigUpdate,
    AIResearchReportCreate,
    AIResearchReportSchema,
)

router = APIRouter(prefix="/ai-research", tags=["ai-research"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + "Z"


def _get_config(db: Session) -> AIResearchConfigModel:
    cfg = db.query(AIResearchConfigModel).filter(AIResearchConfigModel.id == "default").first()
    if not cfg:
        cfg = AIResearchConfigModel(
            id="default",
            topic="Artificial Intelligence",
            set_by="admin",
            enabled=True,
        )
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


def _config_schema(cfg: AIResearchConfigModel) -> AIResearchConfigSchema:
    return AIResearchConfigSchema(
        topic=cfg.topic,
        setBy=cfg.set_by,
        updatedAt=_dt(cfg.updated_at),
        enabled=cfg.enabled,
    )


def _report_schema(r: AIResearchReportModel) -> AIResearchReportSchema:
    return AIResearchReportSchema(
        id=r.id,
        title=r.title,
        source=r.source,
        date=r.date,
        score=r.score,
        link=r.link,
        topic=r.topic,
        createdAt=_dt(r.created_at),
    )


def _idea_schema(i: AIIdeaDropModel) -> AIIdeaDropSchema:
    return AIIdeaDropSchema(
        id=i.id,
        title=i.title,
        hook=i.hook,
        category=i.category,
        priority=i.priority,  # type: ignore[arg-type]
        tags=json.loads(i.tags_json),
        createdBy=i.created_by,
        createdAt=_dt(i.created_at),
    )


def _action_schema(a: AINextActionModel) -> AINextActionSchema:
    return AINextActionSchema(
        id=a.id,
        label=a.label,
        actionType=a.action_type,  # type: ignore[arg-type]
        completed=a.completed,
        createdAt=_dt(a.created_at),
    )


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@router.get("/config", response_model=AIResearchConfigSchema)
async def get_config(
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    return _config_schema(_get_config(db))


@router.put("/config", response_model=AIResearchConfigSchema)
async def update_config(
    payload: AIResearchConfigUpdate,
    db: Session = Depends(get_db),
    admin=Depends(get_current_admin),
):
    cfg = _get_config(db)
    if payload.topic is not None:
        cfg.topic = payload.topic
    if payload.set_by is not None:
        cfg.set_by = payload.set_by
    else:
        cfg.set_by = admin.username
    if payload.enabled is not None:
        cfg.enabled = payload.enabled
    cfg.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(cfg)
    return _config_schema(cfg)


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

@router.get("/reports/health", response_model=AIDbHealthSchema)
async def get_db_health(
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    cfg = _get_config(db)
    total = db.query(AIResearchReportModel).filter(
        AIResearchReportModel.topic == cfg.topic
    ).count()
    latest = (
        db.query(AIResearchReportModel)
        .filter(AIResearchReportModel.topic == cfg.topic)
        .order_by(AIResearchReportModel.created_at.desc())
        .first()
    )
    last_ingest = _dt(latest.created_at) if latest else "Never"
    return AIDbHealthSchema(lastIngest=last_ingest, totalRecords=total)


@router.get("/reports/sources", response_model=List[str])
async def list_sources(
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    cfg = _get_config(db)
    rows = (
        db.query(AIResearchReportModel.source)
        .filter(AIResearchReportModel.topic == cfg.topic)
        .distinct()
        .all()
    )
    return sorted(row[0] for row in rows if row[0])


@router.get("/reports", response_model=List[AIResearchReportSchema])
async def list_reports(
    source: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    cfg = _get_config(db)
    q = db.query(AIResearchReportModel).filter(
        AIResearchReportModel.topic == cfg.topic
    )
    if source:
        q = q.filter(AIResearchReportModel.source == source)
    if keyword:
        q = q.filter(AIResearchReportModel.title.ilike(f"%{keyword}%"))
    reports = q.order_by(AIResearchReportModel.created_at.desc()).all()
    return [_report_schema(r) for r in reports]


@router.post("/reports", response_model=AIResearchReportSchema, status_code=201)
async def create_report(
    payload: AIResearchReportCreate,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    r = AIResearchReportModel(
        id=str(uuid4()),
        title=payload.title,
        source=payload.source,
        date=payload.date,
        score=payload.score,
        link=payload.link,
        topic=payload.topic,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return _report_schema(r)


@router.delete("/reports", status_code=204)
async def clear_reports(
    db: Session = Depends(get_db),
    _user=Depends(get_current_admin),
):
    cfg = _get_config(db)
    db.query(AIResearchReportModel).filter(
        AIResearchReportModel.topic == cfg.topic
    ).delete(synchronize_session=False)
    db.commit()
    return None


@router.delete("/reports/{report_id}", status_code=204)
async def delete_report(
    report_id: str,
    db: Session = Depends(get_db),
    _user=Depends(get_current_admin),
):
    r = db.query(AIResearchReportModel).filter(AIResearchReportModel.id == report_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Report not found")
    db.delete(r)
    db.commit()
    return None


# ---------------------------------------------------------------------------
# Idea Drops
# ---------------------------------------------------------------------------

@router.get("/idea-drops", response_model=List[AIIdeaDropSchema])
async def list_idea_drops(
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    ideas = db.query(AIIdeaDropModel).order_by(AIIdeaDropModel.created_at.desc()).all()
    return [_idea_schema(i) for i in ideas]


@router.post("/idea-drops", response_model=AIIdeaDropSchema, status_code=201)
async def create_idea_drop(
    payload: AIIdeaDropCreate,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    idea = AIIdeaDropModel(
        id=str(uuid4()),
        title=payload.title,
        hook=payload.hook,
        category=payload.category,
        priority=payload.priority,
        tags_json=json.dumps(payload.tags),
        created_by=user.username,
    )
    db.add(idea)
    db.commit()
    db.refresh(idea)
    return _idea_schema(idea)


@router.patch("/idea-drops/{idea_id}", response_model=AIIdeaDropSchema)
async def update_idea_drop(
    idea_id: str,
    payload: AIIdeaDropUpdate,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    idea = db.query(AIIdeaDropModel).filter(AIIdeaDropModel.id == idea_id).first()
    if not idea:
        raise HTTPException(status_code=404, detail="Idea drop not found")
    if payload.title is not None:
        idea.title = payload.title
    if payload.hook is not None:
        idea.hook = payload.hook
    if payload.category is not None:
        idea.category = payload.category
    if payload.priority is not None:
        idea.priority = payload.priority
    if payload.tags is not None:
        idea.tags_json = json.dumps(payload.tags)
    db.commit()
    db.refresh(idea)
    return _idea_schema(idea)


@router.delete("/idea-drops/{idea_id}", status_code=204)
async def delete_idea_drop(
    idea_id: str,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    idea = db.query(AIIdeaDropModel).filter(AIIdeaDropModel.id == idea_id).first()
    if not idea:
        raise HTTPException(status_code=404, detail="Idea drop not found")
    db.delete(idea)
    db.commit()
    return None


# ---------------------------------------------------------------------------
# Next Actions
# ---------------------------------------------------------------------------

@router.get("/next-actions", response_model=List[AINextActionSchema])
async def list_next_actions(
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    actions = db.query(AINextActionModel).order_by(AINextActionModel.created_at).all()
    return [_action_schema(a) for a in actions]


@router.post("/next-actions", response_model=AINextActionSchema, status_code=201)
async def create_next_action(
    payload: AINextActionCreate,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    action = AINextActionModel(
        id=str(uuid4()),
        label=payload.label,
        action_type=payload.action_type,
        completed=False,
    )
    db.add(action)
    db.commit()
    db.refresh(action)
    return _action_schema(action)


@router.patch("/next-actions/{action_id}", response_model=AINextActionSchema)
async def update_next_action(
    action_id: str,
    payload: AINextActionUpdate,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    action = db.query(AINextActionModel).filter(AINextActionModel.id == action_id).first()
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    if payload.completed is not None:
        action.completed = payload.completed
    if payload.action_type is not None:
        action.action_type = payload.action_type
    if payload.label is not None:
        action.label = payload.label
    db.commit()
    db.refresh(action)
    return _action_schema(action)


@router.delete("/next-actions/{action_id}", status_code=204)
async def delete_next_action(
    action_id: str,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    action = db.query(AINextActionModel).filter(AINextActionModel.id == action_id).first()
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    db.delete(action)
    db.commit()
    return None


# ---------------------------------------------------------------------------
# Digest  (LLM generates summary from current reports)
# ---------------------------------------------------------------------------

@router.post("/digest", response_model=AIDigestPayload)
async def generate_digest(
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    from .llm import chat_logged  # noqa: PLC0415

    cfg = _get_config(db)
    reports = (
        db.query(AIResearchReportModel)
        .filter(AIResearchReportModel.topic == cfg.topic)
        .order_by(
            AIResearchReportModel.created_at.desc(),
            AIResearchReportModel.score.desc(),
        )
        .limit(10)
        .all()
    )

    if not reports:
        md = (
            f"# {cfg.topic} Research Digest\n"
            f"_Generated: {datetime.utcnow().strftime('%Y-%m-%d')}_\n\n"
            f"No reports found for this topic yet. Run the research agent to fetch findings."
        )
        return AIDigestPayload(generatedAt=datetime.utcnow().isoformat() + "Z", markdown=md)

    report_text = "\n".join(
        f"- [{r.title}]({r.link}) | Source: {r.source} | Date: {r.date} | Score: {r.score}/100"
        for r in reports
    )
    prompt = (
        f"You are a research analyst. Based on the following recent reports about '{cfg.topic}', "
        f"write a concise daily digest in Markdown format. Include:\n"
        f"1. A brief 2-3 sentence executive summary\n"
        f"2. Top 3 key findings as bullet points\n"
        f"3. 2-3 recommended next steps\n\n"
        f"Reports:\n{report_text}\n\n"
        f"Format as clean Markdown starting with '# {cfg.topic} Research Digest'."
    )

    markdown = chat_logged(prompt, channel="ai-research/digest", max_tokens=1024)
    return AIDigestPayload(generatedAt=datetime.utcnow().isoformat() + "Z", markdown=markdown)


# ---------------------------------------------------------------------------
# Manual agent trigger
# ---------------------------------------------------------------------------

@router.post("/run-agent", status_code=202)
async def run_agent(
    db: Session = Depends(get_db),
    _user=Depends(get_current_admin),
):
    """Manually trigger the AI research job."""
    from .scheduler import run_ai_research_job  # noqa: PLC0415
    import asyncio  # noqa: PLC0415

    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, run_ai_research_job)
    return {"status": "accepted", "message": "AI research job started in background"}


# ---------------------------------------------------------------------------
# Seed sample data
# ---------------------------------------------------------------------------

def _seed():
    from .database import SessionLocal  # noqa: PLC0415

    db = SessionLocal()
    try:
        _get_config(db)

        if db.query(AIIdeaDropModel).count() == 0:
            db.add(AIIdeaDropModel(
                id="ai-1", title="Agent Orchestration Post",
                hook="Why single-agent systems are dead",
                category="LinkedIn", priority="high",
                tags_json='["agents","orchestration"]', created_by="admin",
            ))
            db.add(AIIdeaDropModel(
                id="ai-2", title="RAG vs Fine-Tuning",
                hook="The real tradeoff nobody talks about",
                category="Blog", priority="medium",
                tags_json='["rag","fine-tuning"]', created_by="admin",
            ))

        if db.query(AINextActionModel).count() == 0:
            defaults = [
                AINextActionModel(id="an-1", label="Deep dive into multi-agent patterns", action_type="Research", completed=False),
                AINextActionModel(id="an-2", label="Convert RAG findings to LinkedIn post", action_type="Comment", completed=False),
                AINextActionModel(id="an-3", label="Add prompt engineering to weekly digest", action_type="Forward", completed=True),
            ]
            for a in defaults:
                db.add(a)

        db.commit()
    finally:
        db.close()


_seed()
