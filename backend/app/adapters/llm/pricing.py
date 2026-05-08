"""Per-model token pricing — used to compute `cost_usd` when an LLM provider
doesn't return one in its usage block.

Prices are in **USD per 1,000 tokens** and are best-effort snapshots taken
from each provider's public pricing page. They drift over time; treat them
as guard-rails, not invoices. When billing precision matters, we should
record the raw token counts (which we do) and re-compute against canonical
prices later.

Lookup falls back to a generic "unknown" rate so a model we don't recognise
still produces a non-zero cost the budget guard can act on.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelPrice:
    input_per_1k_usd: float
    output_per_1k_usd: float


# Conservative defaults — used when we don't know the model. Picked to be
# slightly higher than Claude Sonnet so cost guards err on the safe side.
_UNKNOWN = ModelPrice(input_per_1k_usd=0.005, output_per_1k_usd=0.015)


# Keys are case-insensitive substring matches against the model id the
# provider returns. The first match wins, so order most-specific first.
_TABLE: dict[str, ModelPrice] = {
    # ── Anthropic ────────────────────────────────────────────────────────
    "claude-opus-4":    ModelPrice(0.015, 0.075),
    "claude-opus":      ModelPrice(0.015, 0.075),
    "claude-sonnet-4":  ModelPrice(0.003, 0.015),
    "claude-sonnet":    ModelPrice(0.003, 0.015),
    "claude-haiku-4":   ModelPrice(0.001, 0.005),
    "claude-haiku":     ModelPrice(0.00025, 0.00125),
    "claude-3-5-sonnet": ModelPrice(0.003, 0.015),
    "claude-3-opus":    ModelPrice(0.015, 0.075),
    "claude-3-haiku":   ModelPrice(0.00025, 0.00125),
    # ── OpenAI ───────────────────────────────────────────────────────────
    "gpt-4o-mini":      ModelPrice(0.00015, 0.0006),
    "gpt-4o":           ModelPrice(0.005, 0.015),
    "gpt-4-turbo":      ModelPrice(0.01, 0.03),
    "gpt-4":            ModelPrice(0.03, 0.06),
    "gpt-3.5":          ModelPrice(0.0005, 0.0015),
    "o1-mini":          ModelPrice(0.003, 0.012),
    "o1":               ModelPrice(0.015, 0.06),
    # ── Google Gemini ────────────────────────────────────────────────────
    "gemini-1.5-pro":   ModelPrice(0.00125, 0.005),
    "gemini-1.5-flash": ModelPrice(0.000075, 0.0003),
    "gemini-2.0-flash": ModelPrice(0.000075, 0.0003),
    "gemini-2.5-pro":   ModelPrice(0.00125, 0.005),
    "gemini":           ModelPrice(0.00125, 0.005),
    # ── Self-hosted / open / mock ───────────────────────────────────────
    "ollama":           ModelPrice(0.0, 0.0),
    "huggingface":      ModelPrice(0.0, 0.0),
    "mock":             ModelPrice(0.0, 0.0),
}


def lookup(model: str) -> ModelPrice:
    if not model:
        return _UNKNOWN
    m = model.lower()
    for key, price in _TABLE.items():
        if key in m:
            return price
    return _UNKNOWN


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return the dollar cost of a single completion."""
    p = lookup(model)
    return round(
        (max(0, input_tokens) / 1000.0) * p.input_per_1k_usd
        + (max(0, output_tokens) / 1000.0) * p.output_per_1k_usd,
        6,
    )
