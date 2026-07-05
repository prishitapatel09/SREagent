"""LLM client factory: real OpenAI-compatible client, or the deterministic stub.

`make_llm_client` is the only place in the codebase that knows which mode
the agent is running in — everything downstream sees the same duck-typed
`client.chat.completions.create(...)` surface.
"""

import httpx

from ..config import Settings
from .stub import StubLLM


def make_llm_client(settings: Settings):
    if settings.agent_mode == "stub":
        return StubLLM()
    from openai import OpenAI

    return OpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        timeout=settings.llm_timeout_s,
        max_retries=1,
    )


def llm_reachable(settings: Settings, timeout: float = 2.0) -> bool:
    if settings.agent_mode == "stub":
        return True
    try:
        response = httpx.get(
            f"{settings.llm_base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            timeout=timeout,
        )
        return response.status_code < 500
    except httpx.HTTPError:
        return False
