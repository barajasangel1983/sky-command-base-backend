"""
Research modules — generic research tabs.

Each module (tab) has its own topic, reports, idea drops, next actions,
digest and research agent. Modules are rows in `research_modules`, so admins
can add/remove tabs from the UI without code changes.

Routes:
  /research-modules                  list / create modules (admin creates)
  /research-modules/{slug}           rename / re-icon / delete a module (admin)
  /research-modules/{slug}/...       per-module data (config, reports, ...)
  /ai-research/..., /other-research/...
                                     legacy aliases for the modules that
                                     replaced the old hard-coded tabs (used by
                                     OpenClaw tools docs)
"""

import json
import os
import re
from datetime import datetime
from typing import Callable, List, Optional
from uuid import uuid4

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .deps import get_current_admin, get_current_user, get_db
from .models import (
    AIIdeaDrop,
    AINextAction,
    AIResearchConfig,
    AIResearchReport,
    OtherIdeaDrop,
    OtherNextAction,
    OtherResearchConfig,
    OtherResearchReport,
    ResearchIdeaDrop,
    ResearchModule,
    ResearchNextAction,
    ResearchReport,
)
from .schemas import (
    OtherDbHealthSchema as DbHealthSchema,
    OtherDigestPayload as DigestPayload,
    OtherIdeaDropCreate as IdeaDropCreate,
    OtherIdeaDropSchema as IdeaDropSchema,
    OtherIdeaDropUpdate as IdeaDropUpdate,
    OtherNextActionCreate as NextActionCreate,
    OtherNextActionSchema as NextActionSchema,
    OtherNextActionUpdate as NextActionUpdate,
    OtherResearchConfigSchema as ConfigSchema,
    OtherResearchConfigUpdate as ConfigUpdate,
    OtherResearchReportCreate as ReportCreate,
    OtherResearchReportSchema as ReportSchema,
    ResearchModuleCreate,
    ResearchModuleSchema,
    ResearchModuleUpdate,
)

# Icon keys the frontend knows how to render (see src/lib/researchIcons.ts)
ICONS = {"search", "flask", "factory", "network", "layers", "boxes", "cpu", "globe", "book", "lightbulb", "zap", "database"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + "Z"


def _topics(module: ResearchModule) -> list[str]:
    # Topic may be a " | "-joined list (e.g. "Battery Recycling | Manufacturing AI");
    # reports matching any listed topic are shown.
    return [t.strip() for t in module.topic.split("|") if t.strip()]


def _module_schema(m: ResearchModule) -> ResearchModuleSchema:
    return ResearchModuleSchema(
        slug=m.slug, name=m.name, icon=m.icon, topic=m.topic, enabled=m.enabled, sortOrder=m.sort_order,
    )


def _config_schema(m: ResearchModule) -> ConfigSchema:
    return ConfigSchema(topic=m.topic, setBy=m.set_by, updatedAt=_dt(m.updated_at), enabled=m.enabled)


def _report_schema(r: ResearchReport) -> ReportSchema:
    return ReportSchema(
        id=r.id, title=r.title, source=r.source, date=r.date, score=r.score,
        link=r.link, topic=r.topic, createdAt=_dt(r.created_at),
    )


def _idea_schema(i: ResearchIdeaDrop) -> IdeaDropSchema:
    return IdeaDropSchema(
        id=i.id, title=i.title, hook=i.hook, category=i.category,
        priority=i.priority,  # type: ignore[arg-type]
        tags=json.loads(i.tags_json), createdBy=i.created_by, createdAt=_dt(i.created_at),
    )


def _action_schema(a: ResearchNextAction) -> NextActionSchema:
    return NextActionSchema(
        id=a.id, label=a.label,
        actionType=a.action_type,  # type: ignore[arg-type]
        completed=a.completed, createdAt=_dt(a.created_at),
    )


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "research"


def get_module(slug: str, db: Session = Depends(get_db)) -> ResearchModule:
    m = db.query(ResearchModule).filter(ResearchModule.slug == slug).first()
    if not m:
        raise HTTPException(status_code=404, detail=f"Research module '{slug}' not found")
    return m


def _fixed_module(slug: str) -> Callable[..., ResearchModule]:
    def dep(db: Session = Depends(get_db)) -> ResearchModule:
        return get_module(slug, db)
    return dep


# ---------------------------------------------------------------------------
# Module management
# ---------------------------------------------------------------------------

modules_router = APIRouter(prefix="/research-modules", tags=["research-modules"])


@modules_router.get("", response_model=List[ResearchModuleSchema])
async def list_modules(db: Session = Depends(get_db), _user=Depends(get_current_user)):
    mods = db.query(ResearchModule).order_by(ResearchModule.sort_order, ResearchModule.created_at).all()
    return [_module_schema(m) for m in mods]


@modules_router.post("", response_model=ResearchModuleSchema, status_code=201)
async def create_module(
    payload: ResearchModuleCreate,
    db: Session = Depends(get_db),
    admin=Depends(get_current_admin),
):
    name, topic = payload.name.strip(), payload.topic.strip()
    if not name or not topic:
        raise HTTPException(status_code=422, detail="Name and topic are required")
    if payload.icon not in ICONS:
        raise HTTPException(status_code=422, detail=f"Unknown icon '{payload.icon}'")

    base = _slugify(name)
    slug, n = base, 2
    while db.query(ResearchModule).filter(ResearchModule.slug == slug).first():
        slug, n = f"{base}-{n}", n + 1

    last = db.query(ResearchModule).order_by(ResearchModule.sort_order.desc()).first()
    m = ResearchModule(
        slug=slug, name=name, icon=payload.icon, topic=topic, set_by=admin.username,
        enabled=True, sort_order=(last.sort_order + 1) if last else 0,
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return _module_schema(m)


@modules_router.patch("/{slug}", response_model=ResearchModuleSchema)
async def update_module(
    payload: ResearchModuleUpdate,
    module: ResearchModule = Depends(get_module),
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
):
    if payload.name is not None:
        if not payload.name.strip():
            raise HTTPException(status_code=422, detail="Name cannot be empty")
        module.name = payload.name.strip()
    if payload.icon is not None:
        if payload.icon not in ICONS:
            raise HTTPException(status_code=422, detail=f"Unknown icon '{payload.icon}'")
        module.icon = payload.icon
    if payload.sort_order is not None:
        module.sort_order = payload.sort_order
    module.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(module)
    return _module_schema(module)


@modules_router.delete("/{slug}", status_code=204)
async def delete_module(
    module: ResearchModule = Depends(get_module),
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin),
):
    """Delete a research tab and all of its reports, idea drops and next actions."""
    from .scheduler import get_research_status  # noqa: PLC0415

    if get_research_status(module.slug)["state"] == "running":
        raise HTTPException(status_code=409, detail="Research agent is running for this tab — try again when it finishes")
    for model in (ResearchReport, ResearchIdeaDrop, ResearchNextAction):
        db.query(model).filter(model.module_slug == module.slug).delete(synchronize_session=False)
    db.delete(module)
    db.commit()
    return None


# ---------------------------------------------------------------------------
# Per-module data endpoints
# ---------------------------------------------------------------------------

def make_module_router(prefix: str, module_dep: Callable[..., ResearchModule], **kwargs) -> APIRouter:
    """Build the per-module endpoints under `prefix`, resolving the module via `module_dep`."""
    router = APIRouter(prefix=prefix, **kwargs)

    # --- Config ---

    @router.get("/config", response_model=ConfigSchema)
    async def get_config(module: ResearchModule = Depends(module_dep), _user=Depends(get_current_user)):
        return _config_schema(module)

    @router.put("/config", response_model=ConfigSchema)
    async def update_config(
        payload: ConfigUpdate,
        module: ResearchModule = Depends(module_dep),
        db: Session = Depends(get_db),
        admin=Depends(get_current_admin),
    ):
        if payload.topic is not None:
            if not payload.topic.strip():
                raise HTTPException(status_code=422, detail="Topic cannot be empty")
            module.topic = payload.topic.strip()
        module.set_by = payload.set_by if payload.set_by is not None else admin.username
        if payload.enabled is not None:
            module.enabled = payload.enabled
        module.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(module)
        return _config_schema(module)

    # --- Reports ---

    def _reports_query(db: Session, module: ResearchModule):
        return db.query(ResearchReport).filter(
            ResearchReport.module_slug == module.slug,
            ResearchReport.topic.in_(_topics(module)),
        )

    @router.get("/reports/health", response_model=DbHealthSchema)
    async def get_db_health(
        module: ResearchModule = Depends(module_dep),
        db: Session = Depends(get_db),
        _user=Depends(get_current_user),
    ):
        q = _reports_query(db, module)
        latest = q.order_by(ResearchReport.created_at.desc()).first()
        return DbHealthSchema(lastIngest=_dt(latest.created_at) if latest else "Never", totalRecords=q.count())

    @router.get("/reports/sources", response_model=List[str])
    async def list_sources(
        module: ResearchModule = Depends(module_dep),
        db: Session = Depends(get_db),
        _user=Depends(get_current_user),
    ):
        rows = _reports_query(db, module).with_entities(ResearchReport.source).distinct().all()
        return sorted(row[0] for row in rows if row[0])

    @router.get("/reports", response_model=List[ReportSchema])
    async def list_reports(
        source: Optional[str] = Query(None),
        keyword: Optional[str] = Query(None),
        module: ResearchModule = Depends(module_dep),
        db: Session = Depends(get_db),
        _user=Depends(get_current_user),
    ):
        q = _reports_query(db, module)
        if source:
            q = q.filter(ResearchReport.source == source)
        if keyword:
            q = q.filter(ResearchReport.title.ilike(f"%{keyword}%"))
        return [_report_schema(r) for r in q.order_by(ResearchReport.created_at.desc()).all()]

    @router.post("/reports", response_model=ReportSchema, status_code=201)
    async def create_report(
        payload: ReportCreate,
        module: ResearchModule = Depends(module_dep),
        db: Session = Depends(get_db),
        _user=Depends(get_current_user),
    ):
        r = ResearchReport(
            id=str(uuid4()), module_slug=module.slug, title=payload.title, source=payload.source,
            date=payload.date, score=payload.score, link=payload.link, topic=payload.topic,
        )
        db.add(r)
        db.commit()
        db.refresh(r)
        return _report_schema(r)

    @router.delete("/reports", status_code=204)
    async def clear_reports(
        module: ResearchModule = Depends(module_dep),
        db: Session = Depends(get_db),
        _user=Depends(get_current_admin),
    ):
        _reports_query(db, module).delete(synchronize_session=False)
        db.commit()
        return None

    @router.delete("/reports/{report_id}", status_code=204)
    async def delete_report(
        report_id: str,
        module: ResearchModule = Depends(module_dep),
        db: Session = Depends(get_db),
        _user=Depends(get_current_admin),
    ):
        r = db.query(ResearchReport).filter(
            ResearchReport.id == report_id, ResearchReport.module_slug == module.slug,
        ).first()
        if not r:
            raise HTTPException(status_code=404, detail="Report not found")
        db.delete(r)
        db.commit()
        return None

    # --- Idea Drops ---

    def _get_idea(db: Session, module: ResearchModule, idea_id: str) -> ResearchIdeaDrop:
        idea = db.query(ResearchIdeaDrop).filter(
            ResearchIdeaDrop.id == idea_id, ResearchIdeaDrop.module_slug == module.slug,
        ).first()
        if not idea:
            raise HTTPException(status_code=404, detail="Idea drop not found")
        return idea

    @router.get("/idea-drops", response_model=List[IdeaDropSchema])
    async def list_idea_drops(
        module: ResearchModule = Depends(module_dep),
        db: Session = Depends(get_db),
        _user=Depends(get_current_user),
    ):
        ideas = (
            db.query(ResearchIdeaDrop)
            .filter(ResearchIdeaDrop.module_slug == module.slug)
            .order_by(ResearchIdeaDrop.created_at.desc())
            .all()
        )
        return [_idea_schema(i) for i in ideas]

    @router.post("/idea-drops", response_model=IdeaDropSchema, status_code=201)
    async def create_idea_drop(
        payload: IdeaDropCreate,
        module: ResearchModule = Depends(module_dep),
        db: Session = Depends(get_db),
        user=Depends(get_current_user),
    ):
        idea = ResearchIdeaDrop(
            id=str(uuid4()), module_slug=module.slug, title=payload.title, hook=payload.hook,
            category=payload.category, priority=payload.priority,
            tags_json=json.dumps(payload.tags), created_by=user.username,
        )
        db.add(idea)
        db.commit()
        db.refresh(idea)
        return _idea_schema(idea)

    @router.patch("/idea-drops/{idea_id}", response_model=IdeaDropSchema)
    async def update_idea_drop(
        idea_id: str,
        payload: IdeaDropUpdate,
        module: ResearchModule = Depends(module_dep),
        db: Session = Depends(get_db),
        _user=Depends(get_current_user),
    ):
        idea = _get_idea(db, module, idea_id)
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
        module: ResearchModule = Depends(module_dep),
        db: Session = Depends(get_db),
        _user=Depends(get_current_user),
    ):
        db.delete(_get_idea(db, module, idea_id))
        db.commit()
        return None

    # --- Next Actions ---

    def _get_action(db: Session, module: ResearchModule, action_id: str) -> ResearchNextAction:
        action = db.query(ResearchNextAction).filter(
            ResearchNextAction.id == action_id, ResearchNextAction.module_slug == module.slug,
        ).first()
        if not action:
            raise HTTPException(status_code=404, detail="Action not found")
        return action

    @router.get("/next-actions", response_model=List[NextActionSchema])
    async def list_next_actions(
        module: ResearchModule = Depends(module_dep),
        db: Session = Depends(get_db),
        _user=Depends(get_current_user),
    ):
        actions = (
            db.query(ResearchNextAction)
            .filter(ResearchNextAction.module_slug == module.slug)
            .order_by(ResearchNextAction.created_at)
            .all()
        )
        return [_action_schema(a) for a in actions]

    @router.post("/next-actions", response_model=NextActionSchema, status_code=201)
    async def create_next_action(
        payload: NextActionCreate,
        module: ResearchModule = Depends(module_dep),
        db: Session = Depends(get_db),
        _user=Depends(get_current_user),
    ):
        action = ResearchNextAction(
            id=str(uuid4()), module_slug=module.slug, label=payload.label,
            action_type=payload.action_type, completed=False,
        )
        db.add(action)
        db.commit()
        db.refresh(action)
        return _action_schema(action)

    @router.patch("/next-actions/{action_id}", response_model=NextActionSchema)
    async def update_next_action(
        action_id: str,
        payload: NextActionUpdate,
        module: ResearchModule = Depends(module_dep),
        db: Session = Depends(get_db),
        _user=Depends(get_current_user),
    ):
        action = _get_action(db, module, action_id)
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
        module: ResearchModule = Depends(module_dep),
        db: Session = Depends(get_db),
        _user=Depends(get_current_user),
    ):
        db.delete(_get_action(db, module, action_id))
        db.commit()
        return None

    # --- Digest (LLM summary of current reports) ---

    @router.post("/digest", response_model=DigestPayload)
    async def generate_digest(
        module: ResearchModule = Depends(module_dep),
        db: Session = Depends(get_db),
        _user=Depends(get_current_user),
    ):
        from .llm import chat_logged  # noqa: PLC0415

        reports = (
            _reports_query(db, module)
            .order_by(ResearchReport.created_at.desc(), ResearchReport.score.desc())
            .limit(10)
            .all()
        )
        now = datetime.utcnow()
        if not reports:
            md = (
                f"# {module.topic} Research Digest\n"
                f"_Generated: {now.strftime('%Y-%m-%d')}_\n\n"
                f"No reports found for this topic yet. Run the research agent to fetch findings."
            )
            return DigestPayload(generatedAt=now.isoformat() + "Z", markdown=md)

        report_text = "\n".join(
            f"- [{r.title}]({r.link}) | Source: {r.source} | Date: {r.date} | Score: {r.score}/100"
            for r in reports
        )
        prompt = (
            f"You are a research analyst. Based on the following recent reports about '{module.topic}', "
            f"write a concise daily digest in Markdown format. Include:\n"
            f"1. A brief 2-3 sentence executive summary\n"
            f"2. Top 3 key findings as bullet points\n"
            f"3. 2-3 recommended next steps\n\n"
            f"Reports:\n{report_text}\n\n"
            f"Format as clean Markdown starting with '# {module.topic} Research Digest'."
        )
        markdown = chat_logged(prompt, channel=f"research/{module.slug}/digest", max_tokens=1024)
        return DigestPayload(generatedAt=now.isoformat() + "Z", markdown=markdown)

    # --- Research agent ---

    @router.post("/run-agent", status_code=202)
    async def run_agent(module: ResearchModule = Depends(module_dep), _user=Depends(get_current_admin)):
        """Manually trigger this module's web research job (same as the daily cron)."""
        from .scheduler import get_research_status, run_research_job, set_job_status  # noqa: PLC0415
        import asyncio  # noqa: PLC0415

        status = get_research_status(module.slug)
        if status["state"] == "running":
            raise HTTPException(status_code=409, detail="Research agent is already running")
        # Mark running before scheduling so an immediate status poll doesn't see a stale result.
        set_job_status(status, "running", "Starting research agent…")
        asyncio.get_event_loop().run_in_executor(None, run_research_job, module.slug)
        return {"status": "accepted", "message": "Research job started in background"}

    @router.get("/agent-status")
    async def get_agent_status(module: ResearchModule = Depends(module_dep), _user=Depends(get_current_user)):
        """Status of the current or most recent research agent run for this module."""
        from .scheduler import get_research_status  # noqa: PLC0415

        return get_research_status(module.slug)

    return router


router = make_module_router("/research-modules/{slug}", get_module, tags=["research-modules"])

# Legacy URLs from before research tabs were generic (referenced in OpenClaw workspace docs)
LEGACY_PREFIXES = {"ai": "/ai-research", "other": "/other-research"}
legacy_routers = [
    make_module_router(prefix, _fixed_module(slug), include_in_schema=False)
    for slug, prefix in LEGACY_PREFIXES.items()
]


# ---------------------------------------------------------------------------
# One-time migration from the old per-tab tables
# ---------------------------------------------------------------------------

def _migrate_legacy() -> None:
    """Create the ai/other modules from the old ai_* / other_* tables on first run.

    Runs only while research_modules is empty; the old tables are left untouched.
    """
    from .database import SessionLocal  # noqa: PLC0415

    legacy = [
        # slug, name, icon, default topic, config, report, idea, action models
        ("ai", "AI Research", "flask", "Artificial Intelligence",
         AIResearchConfig, AIResearchReport, AIIdeaDrop, AINextAction),
        ("other", "Other Research", "search", "Battery Recycling",
         OtherResearchConfig, OtherResearchReport, OtherIdeaDrop, OtherNextAction),
    ]
    db = SessionLocal()
    try:
        if db.query(ResearchModule).count() > 0:
            return
        taken: set[str] = set()

        def _id(old: str) -> str:
            new = old if old not in taken else str(uuid4())
            taken.add(new)
            return new

        for order, (slug, name, icon, default_topic, Cfg, Rep, Idea, Act) in enumerate(legacy):
            cfg = db.query(Cfg).filter(Cfg.id == "default").first()
            db.add(ResearchModule(
                slug=slug, name=name, icon=icon, sort_order=order,
                topic=cfg.topic if cfg else default_topic,
                set_by=cfg.set_by if cfg else "admin",
                enabled=cfg.enabled if cfg else True,
                updated_at=cfg.updated_at if cfg else datetime.utcnow(),
            ))
            db.flush()
            for r in db.query(Rep).all():
                db.add(ResearchReport(
                    id=_id(r.id), module_slug=slug, title=r.title, source=r.source, date=r.date,
                    score=r.score, link=r.link, topic=r.topic, created_at=r.created_at,
                ))
            for i in db.query(Idea).all():
                db.add(ResearchIdeaDrop(
                    id=_id(i.id), module_slug=slug, title=i.title, hook=i.hook, category=i.category,
                    priority=i.priority, tags_json=i.tags_json, created_by=i.created_by, created_at=i.created_at,
                ))
            for a in db.query(Act).all():
                db.add(ResearchNextAction(
                    id=_id(a.id), module_slug=slug, label=a.label, action_type=a.action_type,
                    completed=a.completed, created_at=a.created_at,
                ))
        db.commit()
    finally:
        db.close()


_migrate_legacy()
