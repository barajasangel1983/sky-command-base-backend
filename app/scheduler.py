"""
APScheduler configuration.

Jobs:
  - run_research_job: runs daily at 06:00 UTC
    1. Reads the current research topic from DB
    2. Brave Search — finds recent articles on the topic
    3. Sends results to LLM (Grok 4 or LM Studio) for scoring + structuring
    4. Saves findings as OtherResearchReport rows in DB
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

# In-memory status of the last/current run of each research job, polled by the UI.
# Resets on backend restart.
def _new_status() -> dict:
    return {
        "state": "idle",  # idle | running | success | error
        "message": "",
        "saved": None,
        "startedAt": None,
        "finishedAt": None,
    }


research_status: dict = _new_status()
ai_research_status: dict = _new_status()


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

def run_research_job() -> None:
    from .database import SessionLocal  # noqa: PLC0415
    from .models import (  # noqa: PLC0415
        OtherResearchConfig as OtherResearchConfigModel,
        OtherResearchReport as OtherResearchReportModel,
    )
    from .llm import chat_logged  # noqa: PLC0415

    set_job_status(research_status, "running", "Starting research agent…")
    db = SessionLocal()
    try:
        cfg = db.query(OtherResearchConfigModel).filter(
            OtherResearchConfigModel.id == "default"
        ).first()
        if not cfg or not cfg.enabled:
            logger.info("[ResearchAgent] Disabled or no config — skipping")
            set_job_status(research_status, "error", "Module is disabled — enable it to run the agent")
            return

        topic = cfg.topic
        logger.info("[ResearchAgent] Researching topic: %s", topic)
        set_job_status(research_status, "running", f"Searching the web for '{topic}'…")

        # 1. Brave Search
        results = _brave_search(f"{topic} latest research 2026", count=10)
        if not results:
            logger.warning("[ResearchAgent] No search results returned")
            set_job_status(research_status, "error", "Web search returned no results (check BRAVE_SEARCH_API_KEY and backend logs)")
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

        set_job_status(research_status, "running", f"Scoring {len(results)} results with the LLM…")
        raw = chat_logged(prompt, channel="other-research/agent", max_tokens=1500)

        # Extract JSON array robustly — LLMs often add preamble/postamble
        start = raw.find("[")
        end = raw.rfind("]")
        if start == -1 or end == -1:
            logger.error("[ResearchAgent] No JSON array found in LLM response")
            set_job_status(research_status, "error", "LLM response contained no JSON array (possibly truncated) — see Agent Monitor")
            return
        raw = raw[start:end + 1]

        findings: list = json.loads(raw)

        # 3. Save to DB — deduplicate by link, then enforce 100-record cap
        existing_links: set[str] = {
            r.link
            for r in db.query(OtherResearchReportModel.link)
            .filter(OtherResearchReportModel.topic == topic)
            .all()
        }

        saved = 0
        for f in findings:
            if not isinstance(f, dict) or not f.get("title"):
                continue
            link = f.get("link", "#")
            if link in existing_links or link == "#":
                logger.debug("[ResearchAgent] Skipping duplicate: %s", link)
                continue
            db.add(OtherResearchReportModel(
                id=str(uuid4()),
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
        logger.info("[ResearchAgent] Saved %d new reports for '%s'", saved, topic)
        set_job_status(research_status, "success", _saved_message(saved), saved)

        # Enforce 100-record cap — delete oldest by created_at
        MAX_REPORTS = 100
        total = db.query(OtherResearchReportModel).filter(
            OtherResearchReportModel.topic == topic
        ).count()
        if total > MAX_REPORTS:
            excess = total - MAX_REPORTS
            oldest = (
                db.query(OtherResearchReportModel)
                .filter(OtherResearchReportModel.topic == topic)
                .order_by(OtherResearchReportModel.created_at.asc())
                .limit(excess)
                .all()
            )
            for old in oldest:
                db.delete(old)
            db.commit()
            logger.info("[ResearchAgent] Evicted %d oldest reports (cap=100)", excess)

    except json.JSONDecodeError as exc:
        logger.error("[ResearchAgent] LLM response not valid JSON: %s", exc)
        set_job_status(research_status, "error", f"LLM response was not valid JSON: {exc}")
    except Exception as exc:
        logger.exception("[ResearchAgent] Unexpected error: %s", exc)
        set_job_status(research_status, "error", f"Unexpected error: {exc}")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# AI Research job
# ---------------------------------------------------------------------------

def run_ai_research_job() -> None:
    from .database import SessionLocal  # noqa: PLC0415
    from .models import (  # noqa: PLC0415
        AIResearchConfig as AIResearchConfigModel,
        AIResearchReport as AIResearchReportModel,
    )
    from .llm import chat_logged  # noqa: PLC0415

    set_job_status(ai_research_status, "running", "Starting research agent…")
    db = SessionLocal()
    try:
        cfg = db.query(AIResearchConfigModel).filter(
            AIResearchConfigModel.id == "default"
        ).first()
        if not cfg or not cfg.enabled:
            logger.info("[AIResearchAgent] Disabled or no config — skipping")
            set_job_status(ai_research_status, "error", "Module is disabled — enable it to run the agent")
            return

        topic = cfg.topic
        logger.info("[AIResearchAgent] Researching topic: %s", topic)
        set_job_status(ai_research_status, "running", f"Searching the web for '{topic}'…")

        # 1. Brave Search
        results = _brave_search(f"{topic} latest research 2026", count=10)
        if not results:
            logger.warning("[AIResearchAgent] No search results returned")
            set_job_status(ai_research_status, "error", "Web search returned no results (check BRAVE_SEARCH_API_KEY and backend logs)")
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

        set_job_status(ai_research_status, "running", f"Scoring {len(results)} results with the LLM…")
        raw = chat_logged(prompt, channel="ai-research/agent", max_tokens=1500)

        start = raw.find("[")
        end = raw.rfind("]")
        if start == -1 or end == -1:
            logger.error("[AIResearchAgent] No JSON array found in LLM response")
            set_job_status(ai_research_status, "error", "LLM response contained no JSON array (possibly truncated) — see Agent Monitor")
            return
        raw = raw[start:end + 1]

        findings: list = json.loads(raw)

        # 3. Save to DB — deduplicate by link, then enforce 100-record cap
        existing_links: set[str] = {
            r.link
            for r in db.query(AIResearchReportModel.link)
            .filter(AIResearchReportModel.topic == topic)
            .all()
        }

        saved = 0
        for f in findings:
            if not isinstance(f, dict) or not f.get("title"):
                continue
            link = f.get("link", "#")
            if link in existing_links or link == "#":
                logger.debug("[AIResearchAgent] Skipping duplicate: %s", link)
                continue
            db.add(AIResearchReportModel(
                id=str(uuid4()),
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
        logger.info("[AIResearchAgent] Saved %d new reports for '%s'", saved, topic)
        set_job_status(ai_research_status, "success", _saved_message(saved), saved)

        # Enforce 100-record cap
        MAX_REPORTS = 100
        total = db.query(AIResearchReportModel).filter(
            AIResearchReportModel.topic == topic
        ).count()
        if total > MAX_REPORTS:
            excess = total - MAX_REPORTS
            oldest = (
                db.query(AIResearchReportModel)
                .filter(AIResearchReportModel.topic == topic)
                .order_by(AIResearchReportModel.created_at.asc())
                .limit(excess)
                .all()
            )
            for old in oldest:
                db.delete(old)
            db.commit()
            logger.info("[AIResearchAgent] Evicted %d oldest reports (cap=100)", excess)

    except json.JSONDecodeError as exc:
        logger.error("[AIResearchAgent] LLM response not valid JSON: %s", exc)
        set_job_status(ai_research_status, "error", f"LLM response was not valid JSON: {exc}")
    except Exception as exc:
        logger.exception("[AIResearchAgent] Unexpected error: %s", exc)
        set_job_status(ai_research_status, "error", f"Unexpected error: {exc}")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Scheduler lifecycle
# ---------------------------------------------------------------------------

def start() -> None:
    scheduler.add_job(
        run_research_job,
        trigger=CronTrigger(hour=6, minute=0, timezone="UTC"),
        id="daily_other_research",
        replace_existing=True,
    )
    scheduler.add_job(
        run_ai_research_job,
        trigger=CronTrigger(hour=6, minute=5, timezone="UTC"),
        id="daily_ai_research",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("[Scheduler] Started — other-research at 06:00 UTC, ai-research at 06:05 UTC daily")


def stop() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("[Scheduler] Stopped")
