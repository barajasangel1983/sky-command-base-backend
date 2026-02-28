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
    from .llm import chat  # noqa: PLC0415

    db = SessionLocal()
    try:
        cfg = db.query(OtherResearchConfigModel).filter(
            OtherResearchConfigModel.id == "default"
        ).first()
        if not cfg or not cfg.enabled:
            logger.info("[ResearchAgent] Disabled or no config — skipping")
            return

        topic = cfg.topic
        logger.info("[ResearchAgent] Researching topic: %s", topic)

        # 1. Brave Search
        results = _brave_search(f"{topic} latest research 2026", count=10)
        if not results:
            logger.warning("[ResearchAgent] No search results returned")
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
            f"Return ONLY a valid JSON array (no markdown, no explanation) with up to 8 entries:\n"
            f'[{{"title":"...","source":"arxiv|blog|report|tutorial|news","date":"{today}","score":<0-100>,"link":"https://..."}}]\n\n'
            f"Results:\n{results_text}"
        )

        raw = chat(prompt, max_tokens=1500)

        # Extract JSON array robustly — LLMs often add preamble/postamble
        start = raw.find("[")
        end = raw.rfind("]")
        if start == -1 or end == -1:
            logger.error("[ResearchAgent] No JSON array found in LLM response")
            return
        raw = raw[start:end + 1]

        findings: list = json.loads(raw)

        # 3. Save to DB
        saved = 0
        for f in findings:
            if not isinstance(f, dict) or not f.get("title"):
                continue
            db.add(OtherResearchReportModel(
                id=str(uuid4()),
                title=f.get("title", "Untitled"),
                source=f.get("source", "web"),
                date=f.get("date", today),
                score=int(f.get("score", 70)),
                link=f.get("link", "#"),
                topic=topic,
            ))
            saved += 1

        db.commit()
        logger.info("[ResearchAgent] Saved %d reports for '%s'", saved, topic)

    except json.JSONDecodeError as exc:
        logger.error("[ResearchAgent] LLM response not valid JSON: %s", exc)
    except Exception as exc:
        logger.exception("[ResearchAgent] Unexpected error: %s", exc)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Scheduler lifecycle
# ---------------------------------------------------------------------------

def start() -> None:
    scheduler.add_job(
        run_research_job,
        trigger=CronTrigger(hour=6, minute=0, timezone="UTC"),
        id="daily_research",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("[Scheduler] Started — research job at 06:00 UTC daily")


def stop() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("[Scheduler] Stopped")
