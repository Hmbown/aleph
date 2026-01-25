"""Provider factory."""

from __future__ import annotations

from .anthropic import AnthropicProvider
from .cli import CLIProvider
from .openai import OpenAIProvider
from .base import LLMProvider


PROVIDERS: dict[str, type[LLMProvider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "cli": CLIProvider,
}


def get_provider(name: str, **kwargs: object) -> LLMProvider:
    """Instantiate a provider by name."""

    key = name.lower().strip()
    if key not in PROVIDERS:
        raise ValueError(f"Unknown provider: {name}. Available: {sorted(PROVIDERS.keys())}")
    return PROVIDERS[key](**kwargs)
