"""
APScheduler configuration.

Jobs:
  - run_all_research_jobs: runs daily at 06:00 UTC; for each enabled research
    module (tab), sequentially:
    1. Reads the module's research topic from DB
    2. Brave Search — finds recent articles on the topic
    3. Sends results to the LLM for scoring + structuring
    4. Saves findings as ResearchReport rows in DB
"""

import json
import logging
import os
from datetime import datetime
from uuid import uuid4

import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

logger = logging.getLogger(__name__)
scheduler = BackgroundScheduler(timezone="UTC")

# In-memory status of the last/current run of each research module, keyed by
# module slug and polled by the UI. Resets on backend restart.
_research_statuses: dict[str, dict] = {}


def get_research_status(slug: str) -> dict:
    return _research_statuses.setdefault(slug, {
        "state": "idle",  # idle | running | success | error
        "message": "",
        "saved": None,
        "startedAt": None,
        "finishedAt": None,
    })


def set_job_status(status: dict, state: str, message: str, saved: int | None = None) -> None:
    now = datetime.utcnow().isoformat() + "Z"
    if state == "running":
        status.update(startedAt=now, finishedAt=None)
    else:
        status["finishedAt"] = now
    status.update(state=state, message=message, saved=saved)


def _saved_message(saved: int) -> str:
    if not saved:
        return "No new reports — all results were already saved"
    return f"Saved {saved} new report{'s' if saved != 1 else ''}"


# ---------------------------------------------------------------------------
# Brave Search
# ---------------------------------------------------------------------------

def _brave_search(query: str, count: int = 10) -> list[dict]:
    """
    Returns a list of {title, url, description} from Brave Search.
    Uses freshness=pw (past week) to keep findings recent.
    """
    api_key = os.getenv("BRAVE_SEARCH_API_KEY")
    if not api_key:
        logger.warning("[ResearchAgent] BRAVE_SEARCH_API_KEY not set")
        return []

    try:
        resp = httpx.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": count, "freshness": "pw"},
            headers={
                "X-Subscription-Token": api_key,
                "Accept": "application/json",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("web", {}).get("results", [])
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", "#"),
                "description": r.get("description", ""),
            }
            for r in results
        ]
    except Exception as exc:
        logger.error("[ResearchAgent] Brave Search error: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Research job
# ---------------------------------------------------------------------------

MAX_REPORTS = 100  # per module + topic; oldest evicted first


def run_research_job(slug: str) -> None:
    from .database import SessionLocal  # noqa: PLC0415
    from .models import ResearchModule, ResearchReport  # noqa: PLC0415
    from .llm import chat_logged  # noqa: PLC0415

    status = get_research_status(slug)
    set_job_status(status, "running", "Starting research agent…")
    db = SessionLocal()
    try:
        module = db.query(ResearchModule).filter(ResearchModule.slug == slug).first()
        if not module:
            logger.info("[ResearchAgent:%s] Module not found — skipping", slug)
            set_job_status(status, "error", "Research tab no longer exists")
            return
        if not module.enabled:
            logger.info("[ResearchAgent:%s] Disabled — skipping", slug)
            set_job_status(status, "error", "Module is disabled — enable it to run the agent")
            return

        topic = module.topic
        logger.info("[ResearchAgent:%s] Researching topic: %s", slug, topic)
        set_job_status(status, "running", f"Searching the web for '{topic}'…")

        # 1. Brave Search
        results = _brave_search(f"{topic} latest research 2026", count=10)
        if not results:
            logger.warning("[ResearchAgent:%s] No search results returned", slug)
            set_job_status(status, "error", "Web search returned no results (check BRAVE_SEARCH_API_KEY and backend logs)")
            return

        today = datetime.utcnow().strftime("%Y-%m-%d")
        results_text = "\n".join(
            f"{i+1}. Title: {r['title']}\n   URL: {r['url']}\n   Summary: {r['description']}"
            for i, r in enumerate(results)
        )

        # 2. LLM — score and structure findings
        prompt = (
            f"You are a research analyst. Today is {today}.\n"
            f"Below are web search results about '{topic}'.\n"
            f"Return ONLY a valid JSON array (no markdown, no explanation) with up to 8 entries.\n"
            f"Each entry must have exactly these fields:\n"
            f'  "title": string\n'
            f'  "source": exactly one of: arxiv, blog, report, tutorial, news, web\n'
            f'  "date": "{today}"\n'
            f'  "score": integer 0-100 indicating relevance/quality\n'
            f'  "link": the full URL from the result\n\n'
            f"Results:\n{results_text}"
        )

        set_job_status(status, "running", f"Scoring {len(results)} results with the LLM…")
        raw = chat_logged(prompt, channel=f"research/{slug}/agent", max_tokens=1500)

        # Extract JSON array robustly — LLMs often add preamble/postamble
        start = raw.find("[")
        end = raw.rfind("]")
        if start == -1 or end == -1:
            logger.error("[ResearchAgent:%s] No JSON array found in LLM response", slug)
            set_job_status(status, "error", "LLM response contained no JSON array (possibly truncated) — see Agent Monitor")
            return
        findings: list = json.loads(raw[start:end + 1])

        # 3. Save to DB — deduplicate by link, then enforce the record cap
        same_topic = db.query(ResearchReport).filter(
            ResearchReport.module_slug == slug, ResearchReport.topic == topic,
        )
        existing_links: set[str] = {r.link for r in same_topic.with_entities(ResearchReport.link).all()}

        saved = 0
        for f in findings:
            if not isinstance(f, dict) or not f.get("title"):
                continue
            link = f.get("link", "#")
            if link in existing_links or link == "#":
                logger.debug("[ResearchAgent:%s] Skipping duplicate: %s", slug, link)
                continue
            db.add(ResearchReport(
                id=str(uuid4()),
                module_slug=slug,
                title=f.get("title", "Untitled"),
                source=f.get("source", "web"),
                date=f.get("date", today),
                score=int(f.get("score", 70)),
                link=link,
                topic=topic,
            ))
            existing_links.add(link)
            saved += 1

        db.commit()
        logger.info("[ResearchAgent:%s] Saved %d new reports for '%s'", slug, saved, topic)
        set_job_status(status, "success", _saved_message(saved), saved)

        total = same_topic.count()
        if total > MAX_REPORTS:
            excess = total - MAX_REPORTS
            for old in same_topic.order_by(ResearchReport.created_at.asc()).limit(excess).all():
                db.delete(old)
            db.commit()
            logger.info("[ResearchAgent:%s] Evicted %d oldest reports (cap=%d)", slug, excess, MAX_REPORTS)

    except json.JSONDecodeError as exc:
        logger.error("[ResearchAgent:%s] LLM response not valid JSON: %s", slug, exc)
        set_job_status(status, "error", f"LLM response was not valid JSON: {exc}")
    except Exception as exc:
        logger.exception("[ResearchAgent:%s] Unexpected error: %s", slug, exc)
        set_job_status(status, "error", f"Unexpected error: {exc}")
    finally:
        db.close()


def run_all_research_jobs() -> None:
    """Daily cron: run every enabled module one after another (keeps LLM load sequential)."""
    from .database import SessionLocal  # noqa: PLC0415
    from .models import ResearchModule  # noqa: PLC0415

    with SessionLocal() as db:
        slugs = [
            m.slug for m in db.query(ResearchModule)
            .filter(ResearchModule.enabled.is_(True))
            .order_by(ResearchModule.sort_order)
            .all()
        ]
    for slug in slugs:
        if get_research_status(slug)["state"] == "running":
            logger.info("[ResearchAgent:%s] Already running — skipping scheduled run", slug)
            continue
        run_research_job(slug)


# ---------------------------------------------------------------------------
# Scheduler lifecycle
# ---------------------------------------------------------------------------

def start() -> None:
    scheduler.add_job(
        run_all_research_jobs,
        trigger=CronTrigger(hour=6, minute=0, timezone="UTC"),
        id="daily_research",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("[Scheduler] Started — research modules at 06:00 UTC daily")


def stop() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("[Scheduler] Stopped")
