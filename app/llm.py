"""
Shared LLM client.

Priority:
  1. Grok 4 via xAI  — if XAI_API_KEY is set
  2. LM Studio local — always available fallback
"""

import os
from openai import OpenAI


def get_client() -> tuple[OpenAI, str]:
    """Return (client, model_id) for whichever provider is available."""
    xai_key = os.getenv("XAI_API_KEY")
    if xai_key:
        client = OpenAI(
            base_url="https://api.x.ai/v1",
            api_key=xai_key,
        )
        return client, "grok-4"

    client = OpenAI(
        base_url=os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234/v1"),
        api_key=os.getenv("LMSTUDIO_API_KEY", "lmstudio"),
    )
    return client, os.getenv("LMSTUDIO_MODEL", "local-model")


def chat(prompt: str, max_tokens: int = 1024, timeout: float = 60.0) -> str:
    """Send a single user message and return the assistant text."""
    client, model = get_client()
    response = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        temperature=0.2,
        messages=[{"role": "user", "content": prompt}],
        timeout=timeout,
    )
    return response.choices[0].message.content or ""


def chat_logged(prompt: str, channel: str, max_tokens: int = 1024, timeout: float = 60.0) -> str:
    """Like chat() but records the session in the monitor DB."""
    from uuid import uuid4  # noqa: PLC0415
    from datetime import datetime  # noqa: PLC0415
    from .database import SessionLocal  # noqa: PLC0415
    from .models import (  # noqa: PLC0415
        AgentSession as AgentSessionModel,
        Prompt as PromptModel,
        TimelineEvent as TimelineEventModel,
    )

    db = SessionLocal()
    session_id = str(uuid4())
    client, model = get_client()

    session = AgentSessionModel(
        id=session_id,
        model=model,
        channel=channel,
        status="running",
    )
    db.add(session)

    prompt_ev = TimelineEventModel(
        id=str(uuid4()), session_id=session_id, type="prompt", content=prompt[:4000],
    )
    db.add(prompt_ev)
    prompt_rec = PromptModel(
        id=str(uuid4()), session_id=session_id, role="user", content=prompt[:4000], tokens=0,
    )
    db.add(prompt_rec)
    db.commit()

    try:
        response = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            temperature=0.2,
            messages=[{"role": "user", "content": prompt}],
            timeout=timeout,
        )
        text = response.choices[0].message.content or ""
        usage = response.usage
        tokens_in = usage.prompt_tokens if usage else 0
        tokens_out = usage.completion_tokens if usage else 0

        prompt_rec.tokens = tokens_in
        db.add(TimelineEventModel(
            id=str(uuid4()), session_id=session_id, type="response", content=text[:4000],
        ))
        db.add(PromptModel(
            id=str(uuid4()), session_id=session_id, role="assistant", content=text[:4000], tokens=tokens_out,
        ))
        session.tokens_in = tokens_in
        session.tokens_out = tokens_out
        session.status = "completed"
        session.ended_at = datetime.utcnow()
        db.commit()
        return text

    except Exception as exc:
        session.status = "error"
        session.ended_at = datetime.utcnow()
        db.add(TimelineEventModel(
            id=str(uuid4()), session_id=session_id, type="error", content=str(exc)[:2000],
        ))
        db.commit()
        raise
    finally:
        db.close()
