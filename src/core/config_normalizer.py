from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .settings import Settings
import logging


@dataclass
class NormalizedPrices:
    # Logical sources in desired order (from v2 prices.sources)
    logical_sources: List[str] = field(default_factory=list)
    # Provider priority per logical source (from telemetry.price_source_priority today)
    priority_by_logical: Dict[str, List[str]] = field(default_factory=dict)
    # Duty-cycle threshold for provider health
    duty_cycle_threshold: float = 0.9
    # Endpoints required for some providers
    endpoints: Dict[str, Optional[str]] = field(default_factory=dict)


@dataclass
class TrendIndicatorView:
    enabled: bool = True
    method: str = "polynomial_fit"
    polynomial_order: int = 2
    window_size_hours: float = 24.0
    window_type: str = "polynomial"  # polynomial | gaussian | boxcar | none
    gaussian_kde_sigma_fraction: float = 0.3  # rename of gaussian_std; documented as kernel sigma fraction
    per_asset: Dict[str, dict] = field(default_factory=dict)


@dataclass
class NormalizedAnalysis:
    trend_indicator: TrendIndicatorView = field(default_factory=TrendIndicatorView)
    # Decision and price_compare remain as-is in phase 1; adapters will read from Settings directly.


@dataclass
class NormalizedConfig:
    prices: NormalizedPrices
    analysis: NormalizedAnalysis


def build_normalized_config(settings: Settings) -> NormalizedConfig:
    """
    Build a normalized config view from v2 Settings (v2-only hardening).
    - prices: derived strictly from orchestrator.prices_v2
    - analysis.trend_indicator: prefer v2; limited legacy smoothing merge retained for compatibility with client output
    """
    o = settings.orchestrator
    # v2-only normalized prices
    prices_v2 = getattr(o, 'prices_v2', None)
    if not (prices_v2 and getattr(prices_v2, 'sources', None)):
        raise ValueError("prices_v2 missing or incomplete; v2 schema is required")
    logical_sources = list(getattr(prices_v2, 'sources', []) or [])
    priority_by_logical = dict(getattr(prices_v2, 'priority_by_logical', {}) or {})
    duty = float(getattr(prices_v2, 'duty_cycle_threshold', 0.9) or 0.9)
    endpoints = dict(getattr(prices_v2, 'endpoints', {}) or {})
    prices = NormalizedPrices(
        logical_sources=logical_sources,
        priority_by_logical=priority_by_logical,
        duty_cycle_threshold=duty,
        endpoints=endpoints,
    )

    # Trend indicator
    # Trend indicator: prefer v2 analysis block when available
    tr_v2 = getattr(getattr(o, 'analysis_v2', None), 'trend_indicator', None)
    tr = tr_v2 if tr_v2 is not None else getattr(o, 'trend_indicator', None)
    method = getattr(tr, 'method', 'polynomial_fit') if tr else 'polynomial_fit'
    poly_order = int(getattr(tr, 'polynomial_order', 2) if tr else 2)
    win_h = float(getattr(tr, 'window_size_hours', 24.0) if tr else 24.0)
    # Merge legacy client.output.smoothing if present for window_type/polynomial_order per_asset
    co = getattr(settings.client, 'output', None)
    per_asset: Dict[str, dict] = {}
    window_type = 'polynomial'
    kde_sigma = 0.3
    if tr_v2 is None and co and getattr(co, 'smoothing', None):
        logging.warning("NormalizedConfig: analysis_v2.trend_indicator missing; merging legacy client.output.smoothing into normalized trend view")
        sm = co.smoothing
        # default
        window_type = getattr(sm.default, 'window_type', window_type)
        if window_type == 'polynomial':
            poly_order = int(getattr(sm.default, 'polynomial_order', poly_order))
        # gaussian sigma fraction if present (gaussian_std)
        kde_sigma = float(getattr(sm.default, 'gaussian_std', kde_sigma))
        # per-asset overrides
        if getattr(sm, 'asset_overrides', None):
            for k, v in sm.asset_overrides.items():
                pa: dict = {'window_type': getattr(v, 'window_type', window_type)}
                if pa['window_type'] == 'polynomial':
                    pa['polynomial_order'] = int(getattr(v, 'polynomial_order', poly_order))
                if pa['window_type'] in ('gaussian', 'boxcar'):
                    pa['window_size_hours'] = float(getattr(v, 'window_size_hours', win_h))
                per_asset[str(k).lower()] = pa

    tiv = TrendIndicatorView(
        enabled=bool(getattr(tr, 'enabled', True) if tr else True),
        method=method,
        polynomial_order=poly_order,
        window_size_hours=win_h,
        window_type=window_type,
        gaussian_kde_sigma_fraction=kde_sigma,
        per_asset=per_asset,
    )

    return NormalizedConfig(
        prices=prices,
        analysis=NormalizedAnalysis(trend_indicator=tiv),
    )
