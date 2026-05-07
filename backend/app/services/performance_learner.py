"""PerformanceLearner — closes the loop between the Evaluator's
*predicted* engagement scores and the *actual* metrics fetched after
publish.

The model is a tiny linear regression with closed-form least-squares —
no scikit-learn dependency. We deliberately keep it small because:
  * each org has O(100s) of posts to learn from, not millions
  * we want the learner to be auditable and explainable
  * the result feeds *one* number: a per-(plugin, dimension) weight that
    the Evaluator multiplies its raw score by before reporting `overall`

The learner persists `EvaluatorWeights` per (org, plugin) and exposes a
synchronous `predict()` for inference inside the agent pipeline.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable

from app.core.logging import get_logger
from app.domain.entities.post import Post, PostStatus
from app.domain.value_objects.ids import OrgId
from app.repositories.ports import PlatformRepository, PostRepository

log = get_logger(__name__)


# Default starting weights — used until enough data is collected.
DEFAULT_DIMENSIONS = ("clarity", "brand_voice", "engagement_potential",
                      "originality", "hook_strength")
DEFAULT_WEIGHTS: dict[str, float] = {d: 1.0 for d in DEFAULT_DIMENSIONS}

# Minimum number of (predicted, actual) pairs before we trust the regression.
MIN_SAMPLES = 12


@dataclass(slots=True)
class EvaluatorWeights:
    """Per-(org, plugin) calibrated weights. The Evaluator looks these up
    when computing the `overall` score."""
    org_id: OrgId
    plugin_name: str
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    intercept: float = 0.0
    sample_count: int = 0
    r_squared: float = 0.0
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def normalized(self) -> dict[str, float]:
        """Sum-to-1 normalisation so the Evaluator's blended score stays
        in [0, 1]."""
        total = sum(max(w, 0.0) for w in self.weights.values()) or 1.0
        return {k: max(v, 0.0) / total for k, v in self.weights.items()}


class PerformanceLearner:
    """In-memory learner with a pluggable persistence hook (the production
    deployment can swap `_store` for a Supabase-backed dict)."""

    def __init__(
        self,
        post_repo: PostRepository,
        platform_repo: PlatformRepository,
        *,
        lookback_days: int = 90,
        engagement_metric_keys: tuple[str, ...] = (
            "engagement_rate", "engagement_pct",
        ),
    ) -> None:
        self.post_repo = post_repo
        self.platform_repo = platform_repo
        self.lookback_days = lookback_days
        self.engagement_metric_keys = engagement_metric_keys
        self._store: dict[tuple[OrgId, str], EvaluatorWeights] = {}

    # ── public API ────────────────────────────────────────────────────
    async def get_weights(
        self, org_id: OrgId, plugin_name: str,
    ) -> EvaluatorWeights:
        key = (org_id, plugin_name)
        if key in self._store:
            return self._store[key]
        # Cold-start with defaults.
        w = EvaluatorWeights(org_id=org_id, plugin_name=plugin_name)
        self._store[key] = w
        return w

    async def fit(
        self, org_id: OrgId, *, now: datetime | None = None,
    ) -> list[EvaluatorWeights]:
        """Refit per-plugin weights from the last `lookback_days` of posts
        with both an evaluation and a metrics snapshot."""
        now = now or datetime.utcnow()
        cutoff = now - timedelta(days=self.lookback_days)
        posts = await self.post_repo.list(org_id, status=PostStatus.PUBLISHED.value)
        # Group by plugin via the post's platform.
        bucket: dict[str, list[Post]] = defaultdict(list)
        for p in posts:
            if p.published_at is None or p.published_at < cutoff:
                continue
            if not p.evaluation:
                continue
            actual = self._extract_engagement(p.metrics)
            if actual is None:
                continue
            platform = await self.platform_repo.get(org_id, p.platform_id)
            if not platform:
                continue
            bucket[platform.plugin_name].append(p)

        out: list[EvaluatorWeights] = []
        for plugin_name, plist in bucket.items():
            if len(plist) < MIN_SAMPLES:
                log.info(
                    "performance_learner_skipped_low_samples",
                    plugin=plugin_name, samples=len(plist),
                )
                # Still surface the cold-start record so the API can show
                # the user "not enough data yet".
                w = await self.get_weights(org_id, plugin_name)
                w.sample_count = len(plist)
                self._store[(org_id, plugin_name)] = w
                out.append(w)
                continue
            weights, intercept, r_squared = _fit_least_squares(plist)
            w = EvaluatorWeights(
                org_id=org_id,
                plugin_name=plugin_name,
                weights=weights,
                intercept=intercept,
                sample_count=len(plist),
                r_squared=r_squared,
                updated_at=now,
            )
            self._store[(org_id, plugin_name)] = w
            out.append(w)
            log.info(
                "performance_learner_refit",
                plugin=plugin_name, samples=len(plist),
                r2=round(r_squared, 3),
                weights={k: round(v, 3) for k, v in weights.items()},
            )
        return out

    async def fit_all_orgs(
        self, org_ids: Iterable[OrgId], *, now: datetime | None = None,
    ) -> dict[OrgId, list[EvaluatorWeights]]:
        out: dict[OrgId, list[EvaluatorWeights]] = {}
        for oid in org_ids:
            out[oid] = await self.fit(oid, now=now)
        return out

    def predict(
        self, weights: EvaluatorWeights, dimension_scores: dict[str, float],
    ) -> float:
        """Apply the learned blend; clamp to [0, 1]."""
        norm = weights.normalized()
        blended = weights.intercept
        for dim, w in norm.items():
            blended += w * float(dimension_scores.get(dim, 0.0))
        return max(0.0, min(1.0, blended))

    # ── internals ─────────────────────────────────────────────────────
    def _extract_engagement(self, metrics: dict | None) -> float | None:
        if not metrics:
            return None
        for key in self.engagement_metric_keys:
            v = metrics.get(key)
            if isinstance(v, (int, float)):
                return float(v)
        # Fallback: derive a crude rate from likes/impressions if both exist.
        likes = metrics.get("likes")
        impressions = metrics.get("impressions") or metrics.get("views")
        if isinstance(likes, (int, float)) and isinstance(impressions, (int, float)) and impressions > 0:
            return float(likes) / float(impressions)
        return None


# ── numerics: closed-form OLS without numpy ───────────────────────────────
def _fit_least_squares(
    posts: list[Post],
) -> tuple[dict[str, float], float, float]:
    """Solve weights = (XᵀX + λI)^-1 Xᵀy using the normal equations.

    We add a tiny ridge term to keep things numerically stable when a
    dimension is constant.
    """
    dims = list(DEFAULT_DIMENSIONS)
    rows: list[list[float]] = []
    targets: list[float] = []
    for p in posts:
        ev = p.evaluation
        if ev is None:
            continue
        scores = _evaluation_dimension_scores(ev)
        rows.append([float(scores.get(d, 0.0)) for d in dims] + [1.0])  # +intercept
        # Engagement rate target — already in [0, 1] if computed properly,
        # otherwise rescale by 1.0 (clamped). Caller already filtered None.
        actual = (p.metrics or {}).get("engagement_rate")
        if not isinstance(actual, (int, float)):
            actual = 0.0
        targets.append(float(actual))

    if not rows:
        return dict(DEFAULT_WEIGHTS), 0.0, 0.0

    n_features = len(rows[0])
    # Build XᵀX + λI and Xᵀy
    XtX = [[0.0] * n_features for _ in range(n_features)]
    Xty = [0.0] * n_features
    for r, t in zip(rows, targets):
        for i in range(n_features):
            Xty[i] += r[i] * t
            for j in range(n_features):
                XtX[i][j] += r[i] * r[j]
    ridge = 1e-3
    for i in range(n_features):
        XtX[i][i] += ridge
    coeffs = _solve_linear_system(XtX, Xty)

    weights = {dim: coeffs[i] for i, dim in enumerate(dims)}
    intercept = coeffs[-1]
    r_squared = _r_squared(rows, targets, coeffs)
    return weights, intercept, r_squared


def _solve_linear_system(A: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting."""
    n = len(A)
    # Create an augmented matrix copy so we don't mutate caller state.
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for col in range(n):
        # Pivot: row with largest absolute value in this column at-or-below.
        pivot = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[pivot][col]) < 1e-12:
            # Singular; fall back to identity (returns the offset only).
            return [0.0] * n
        if pivot != col:
            M[col], M[pivot] = M[pivot], M[col]
        # Normalize row.
        pivot_val = M[col][col]
        for k in range(col, n + 1):
            M[col][k] /= pivot_val
        # Eliminate other rows.
        for r in range(n):
            if r == col:
                continue
            factor = M[r][col]
            if factor == 0.0:
                continue
            for k in range(col, n + 1):
                M[r][k] -= factor * M[col][k]
    return [M[i][n] for i in range(n)]


def _r_squared(
    rows: list[list[float]], targets: list[float], coeffs: list[float],
) -> float:
    if not targets:
        return 0.0
    mean_t = sum(targets) / len(targets)
    ss_tot = sum((t - mean_t) ** 2 for t in targets) or 1e-12
    ss_res = 0.0
    for r, t in zip(rows, targets):
        pred = sum(c * v for c, v in zip(coeffs, r))
        ss_res += (t - pred) ** 2
    return max(0.0, 1.0 - ss_res / ss_tot)


def _evaluation_dimension_scores(ev) -> dict[str, float]:
    """Pull the dimension-by-dimension scores out of an `EvaluationReport`.

    The base `EvaluationReport` exposes `clarity`, `brand_voice`,
    `engagement_potential`, `originality`, `hook_strength` (some optional);
    missing dimensions are treated as 0.
    """
    out: dict[str, float] = {}
    for d in DEFAULT_DIMENSIONS:
        v = getattr(ev, d, None)
        if isinstance(v, (int, float)):
            out[d] = float(v)
        else:
            extras = getattr(ev, "extras", None) or getattr(ev, "extra", None)
            if isinstance(extras, dict) and isinstance(extras.get(d), (int, float)):
                out[d] = float(extras[d])
            else:
                out[d] = 0.0
    return out
