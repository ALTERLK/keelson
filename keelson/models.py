"""What Keelson knows about each model: window, output limit, thinking switch, price.

One table instead of model-name checks spread across the agent. Unknown models
get conservative defaults (128k context, 8k output), and two environment
variables override whatever the table says:

    KEELSON_CONTEXT_WINDOW      tokens of context Keelson plans against
    KEELSON_MAX_OUTPUT_TOKENS   max_tokens sent with each request

DeepSeek facts below are from https://api-docs.deepseek.com (models & pricing,
thinking mode), checked 2026-11-17. Prices change; ``PRICES_CHECKED`` says when.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_MODEL = "deepseek-flash"
PRICES_CHECKED = "2026-11-17"


@dataclass(frozen=True)
class Price:
    """USD per million tokens, at the provider's standard (peak) rate."""

    cache_hit: float
    cache_miss: float
    output: float
    # Fraction of the standard price charged off-peak (DeepSeek: half price).
    off_peak_factor: float = 1.0


@dataclass(frozen=True)
class ModelInfo:
    family: str                 # "deepseek" | "claude" | "openai" | "unknown"
    context: int                # the provider's hard limit
    max_output: int             # max_tokens Keelson requests
    # The window Keelson manages against. A 1M window is a ceiling, not a target:
    # folding at 700k tokens would make every call slow and costly.
    budget: int | None = None
    price: Price | None = None
    retired: str = ""           # date the provider stopped serving it, if it has
    replacement: str = ""


_DEEPSEEK_BUDGET = 256_000

# Matched in order: exact name first, then the first prefix that fits.
_EXACT: dict[str, ModelInfo] = {
    "deepseek-flash": ModelInfo(
        "deepseek", 1_000_000, 65_536, budget=_DEEPSEEK_BUDGET,
        price=Price(cache_hit=0.006, cache_miss=0.30, output=1.20, off_peak_factor=0.5),
    ),
    "deepseek-v4-pro": ModelInfo(
        "deepseek", 1_000_000, 65_536, budget=_DEEPSEEK_BUDGET,
        price=Price(cache_hit=0.044, cache_miss=1.32, output=3.96, off_peak_factor=0.5),
    ),
    # Names some gateways (e.g. 4SAPI) use for the same models; price shown is DeepSeek's own.
    "deepseek-v4.1-flash": ModelInfo(
        "deepseek", 1_000_000, 65_536, budget=_DEEPSEEK_BUDGET,
        price=Price(cache_hit=0.006, cache_miss=0.30, output=1.20, off_peak_factor=0.5),
    ),
    "deepseek-v4.1-flash-1017": ModelInfo(
        "deepseek", 1_000_000, 65_536, budget=_DEEPSEEK_BUDGET,
        price=Price(cache_hit=0.006, cache_miss=0.30, output=1.20, off_peak_factor=0.5),
    ),
    "deepseek-v4-pro-0813": ModelInfo(
        "deepseek", 1_000_000, 65_536, budget=_DEEPSEEK_BUDGET,
        price=Price(cache_hit=0.044, cache_miss=1.32, output=3.96, off_peak_factor=0.5),
    ),
    # Retired by DeepSeek after 2026-07-24; requests to them fail.
    "deepseek-chat": ModelInfo("deepseek", 128_000, 8_192, retired="2026-07-24", replacement="deepseek-flash"),
    "deepseek-reasoner": ModelInfo("deepseek", 128_000, 8_192, retired="2026-07-24", replacement="deepseek-v4-pro"),
    "claude-opus-4-6": ModelInfo("claude", 200_000, 64_000),
    "claude-sonnet-4-6": ModelInfo("claude", 200_000, 32_000),
    "gpt-4o": ModelInfo("openai", 128_000, 16_384),
    "gpt-4o-mini": ModelInfo("openai", 128_000, 16_384),
}

_PREFIX: list[tuple[str, ModelInfo]] = [
    ("deepseek-", ModelInfo("deepseek", 1_000_000, 65_536, budget=_DEEPSEEK_BUDGET)),
    ("claude-3", ModelInfo("claude", 200_000, 8_192)),
    ("claude-", ModelInfo("claude", 200_000, 32_000)),
    ("gpt-4o", ModelInfo("openai", 128_000, 16_384)),
]

_UNKNOWN = ModelInfo("unknown", 128_000, 8_192)


def info(model: str) -> ModelInfo:
    name = (model or "").strip().lower()
    if name in _EXACT:
        return _EXACT[name]
    for prefix, entry in _PREFIX:
        if name.startswith(prefix):
            return entry
    return _UNKNOWN


def _env_int(name: str) -> int | None:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def context_window(model: str) -> int:
    """Tokens of context Keelson plans against (compaction thresholds are fractions of this)."""
    override = _env_int("KEELSON_CONTEXT_WINDOW")
    if override:
        return override
    entry = info(model)
    return min(entry.context, entry.budget) if entry.budget else entry.context


def max_output_tokens(model: str) -> int:
    return _env_int("KEELSON_MAX_OUTPUT_TOKENS") or info(model).max_output


def is_deepseek(model: str) -> bool:
    """A model served by DeepSeek's own API under its own name (not a gateway alias)."""
    return info(model).family == "deepseek"


def retirement_note(model: str) -> str:
    """Empty, or a one-line explanation that this model no longer exists."""
    entry = info(model)
    if not entry.retired:
        return ""
    return (f"{model} was retired by its provider on {entry.retired}; "
            f"use {entry.replacement or 'a current model'} instead.")


def cost_usd(model: str, *, cache_hit: int, cache_miss: int, output: int, off_peak: bool = False) -> float | None:
    """Estimated cost of one call, or None when the price is not known."""
    price = info(model).price
    if price is None:
        return None
    factor = price.off_peak_factor if off_peak else 1.0
    total = cache_hit * price.cache_hit + cache_miss * price.cache_miss + output * price.output
    return total * factor / 1_000_000
