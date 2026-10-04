"""How does latency respond to data growth? Fit latency ~ factor^k."""

from __future__ import annotations

import math

from dbscale.core.experiment import ScalingSummary, WorkloadResult

_MIN_LATENCY_MS = 0.05


def fit_exponent(points: list[tuple[float, float]]) -> float | None:
    """Least-squares slope of log(latency) vs log(factor). None if <2 distinct factors."""
    cleaned = [(f, max(lat, _MIN_LATENCY_MS)) for f, lat in points if f > 0]
    factors = {round(f, 9) for f, _ in cleaned}
    if len(factors) < 2:
        return None
    xs = [math.log(f) for f, _ in cleaned]
    ys = [math.log(lat) for _, lat in cleaned]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    var = sum((x - mean_x) ** 2 for x in xs)
    if var == 0:
        return None
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    return cov / var


def compute_scaling(result: WorkloadResult) -> ScalingSummary:
    ok = sorted(result.successful, key=lambda m: m.scale_factor)
    if not ok:
        return ScalingSummary()
    first, last = ok[0], ok[-1]
    summary = ScalingSummary(
        first_label=first.scale_label,
        last_label=last.scale_label,
        first_p50_ms=first.latency.p50_ms if first.latency else None,
        last_p50_ms=last.latency.p50_ms if last.latency else None,
    )
    exponent = fit_exponent([(m.scale_factor, m.latency.p50_ms) for m in ok if m.latency])
    summary.exponent = exponent
    if exponent is not None and summary.last_p50_ms is not None:
        summary.projected_next_10x_ms = summary.last_p50_ms * (10 ** max(exponent, 0.0))
    return summary
