"""Rough call-count and cost estimate for a live run, printed before anything is spent.

Call counts are exact for the configured loop. Token counts are estimates:
memory size varies, and adaptive thinking tokens are billed as output but
their number is unknown in advance, so ``thinking_factor`` multiplies the
visible output. Treat the dollar figure as an order of magnitude.
"""

from __future__ import annotations

from dataclasses import dataclass

# USD per million tokens (input, output), first-party API list prices.
PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}
TOKENS_PER_NOTE = 30


@dataclass(frozen=True)
class RoleEstimate:
    role: str
    model: str
    calls: int
    input_tokens: int
    output_tokens: int

    @property
    def cost(self) -> float | None:
        price = PRICES.get(self.model)
        if price is None:
            return None
        return (self.input_tokens * price[0] + self.output_tokens * price[1]) / 1e6


def estimate(cfg, *, consolidator: str | None, agent: str | None, judge: str | None,
             semantic: str | None, thinking_factor: float = 3.0) -> list[RoleEstimate]:
    """Each argument is the model name used for that role, or None if the role is simulated."""
    mem = cfg.capacity * TOKENS_PER_NOTE
    measures = cfg.cycles + 1
    per_trial = {
        "consolidator": (cfg.cycles, 400 + int(mem * 1.3), int(mem * thinking_factor)),
        "agent": (cfg.cycles * cfg.interactions_per_cycle + measures * cfg.probes_per_cycle,
                  250 + mem, int(120 * thinking_factor)),
        "judge": (measures * cfg.probes_per_cycle, 300, int(10 * thinking_factor)),
        "semantic": (measures * 2, 300 + mem, int(10 * thinking_factor)),
    }
    models = {"consolidator": consolidator, "agent": agent, "judge": judge, "semantic": semantic}
    out = []
    for role, (calls, tin, tout) in per_trial.items():
        model = models[role]
        if model is None:
            continue
        n = calls * cfg.trials + (2 * cfg.calibration_n if role == "judge" else 0)
        out.append(RoleEstimate(role, model, n, n * tin, n * tout))
    return out


def format_estimate(rows: list[RoleEstimate]) -> str:
    if not rows:
        return "No model calls: every component is simulated."
    lines = [f"{'role':<13}{'model':<20}{'calls':>9}{'input tok':>12}{'output tok':>12}{'est. $':>10}"]
    total_calls, total_cost, unknown = 0, 0.0, False
    for r in rows:
        c = r.cost
        unknown |= c is None
        total_calls += r.calls
        total_cost += c or 0.0
        lines.append(f"{r.role:<13}{r.model:<20}{r.calls:>9,}{r.input_tokens:>12,}{r.output_tokens:>12,}"
                     f"{'?' if c is None else f'{c:,.2f}':>10}")
    lines.append(f"{'total':<33}{total_calls:>9,}{'':>24}{('≥' if unknown else '') + f'{total_cost:,.2f}':>10}")
    lines.append("Estimate only: thinking tokens and memory size vary. Cached responses are free on resume.")
    return "\n".join(lines)
