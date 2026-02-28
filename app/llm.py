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
        base_url=os.getenv("LMSTUDIO_BASE_URL", "http://100.111.50.52:1234/v1"),
        api_key=os.getenv("LMSTUDIO_API_KEY", "lmstudio"),
    )
    return client, os.getenv("LMSTUDIO_MODEL", "llama-3-8b-instruct-64k")


def chat(prompt: str, max_tokens: int = 1024) -> str:
    """Send a single user message and return the assistant text."""
    client, model = get_client()
    response = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content or ""
