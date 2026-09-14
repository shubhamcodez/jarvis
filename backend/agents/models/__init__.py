"""Models / API clients (OpenAI, xAI Grok, local Hugging Face)."""
from . import local_client
from . import openai_client
from . import xai_client


def get_llm_client(provider: str):
    """Return the client module for the given provider: 'openai', 'xai', or 'local'."""
    p = (provider or "").strip().lower()
    if p == "xai":
        return xai_client
    if p == "local":
        return local_client
    return openai_client


# Re-export for code that imports by name
from .openai_client import (
    chat,
    chat_stream,
    chat_completion_limit_kwargs,
    classify_task,
    should_omit_temperature,
    vision_desktop_action,
)

__all__ = [
    "chat",
    "chat_stream",
    "chat_completion_limit_kwargs",
    "classify_task",
    "should_omit_temperature",
    "vision_desktop_action",
    "get_llm_client",
    "openai_client",
    "xai_client",
    "local_client",
]
