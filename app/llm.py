"""
Shared LLM client.

Priority:
  1. Grok 4 via xAI  — if XAI_API_KEY is set
  2. Ollama local    — always available fallback (Ollama on BSK_AI laptop, 100.76.107.3:11434)
     (was LM Studio at 100.111.50.52:1234 — dead as of 2026-09-15)
"""

import os
from openai import OpenAI


def get_client() -> tuple[OpenAI, str]:
    """Return (client, model_id) for whichever provider is available."""
    xai_key = os.getenv("XAI_API_KEY")
    if xai_key and not os.getenv("DISABLE_XAI"):
        client = OpenAI(
            base_url="https://api.x.ai/v1",
            api_key=xai_key,
        )
        return client, "grok-4"

    client = OpenAI(
        base_url=os.getenv("LMSTUDIO_BASE_URL", "http://localhost:11434/v1"),
        api_key=os.getenv("LMSTUDIO_API_KEY", "ollama"),
    )
    return client, os.getenv("LMSTUDIO_MODEL", "qwen3:8b")


def _create_completion(client, model: str, prompt: str, max_tokens: int, timeout: float):
    """Build the chat.completions.create kwargs (adds think=false for Ollama thinking models)."""
    kwargs: dict = dict(
        model=model,
        max_tokens=max_tokens,
        temperature=0.2,
        messages=[{"role": "user", "content": prompt}],
        timeout=timeout,
    )
    if model.startswith("qwen3"):
        # Ollama: thinking mode (on by default) eats the token budget and returns
        # empty/truncated content. Its /v1 endpoint ignores {"think": False};
        # reasoning_effort="none" is what actually disables it.
        kwargs["reasoning_effort"] = "none"
    return client.chat.completions.create(**kwargs)


def chat(prompt: str, max_tokens: int = 1024, timeout: float = 120.0) -> str:
    """Send a single user message and return the assistant text."""
    client, model = get_client()
    response = _create_completion(client, model, prompt, max_tokens, timeout)
    return response.choices[0].message.content or ""


def chat_logged(prompt: str, channel: str, max_tokens: int = 1024, timeout: float = 120.0) -> str:
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
        response = _create_completion(client, model, prompt, max_tokens, timeout)
        text = response.choices[0].message.content or ""
        if not text and getattr(response.choices[0].message, "reasoning", None):
            # Model burned the budget on reasoning; take the tail of it as a last resort.
            text = str(response.choices[0].message.reasoning)[-500:]
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
