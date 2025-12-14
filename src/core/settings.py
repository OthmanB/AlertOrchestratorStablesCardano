#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Orchestrator settings loader

Parses orchestrator-specific configuration while reusing the existing client
configuration loader from src/shared/config.py
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, List
from pathlib import Path
from datetime import datetime, timezone, timedelta
import os
import yaml
import logging
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None  # Fallback if not available

from ..shared.config import (
    load_client_config,
    ClientConfig,
    ConfigError,
    GreptimeConnConfig,
    DateRange,
    OutputConfig,
)

log = logging.getLogger(__name__)


@dataclass
class TelemetryExpose:
    # Control which metrics appear in /metrics (privacy by default)
    decision: Optional[bool] = None
    wmax_usd: Optional[bool] = None
    v_ref_usd: Optional[bool] = None
    v_t1_usd: Optional[bool] = None
    g_usd: Optional[bool] = None
    price_t1_usd: Optional[bool] = None
    t0_timestamp_seconds: Optional[bool] = None
    t1_timestamp_seconds: Optional[bool] = None
    # Residual gate telemetry (opt-in)
    residual_usd: Optional[bool] = None
    sigma_usd: Optional[bool] = None
    k_sigma: Optional[bool] = None
    residual_trigger: Optional[bool] = None
    # Price compare telemetry (opt-in)
    price_usd_by_source: Optional[bool] = None
    price_delta_abs: Optional[bool] = None
    price_delta_rel: Optional[bool] = None
    price_mismatch: Optional[bool] = None
    price_compare_unavailable: Optional[bool] = None
    # Rates telemetry
    rate_usd: Optional[bool] = None
    rate_ada: Optional[bool] = None


@dataclass
class TelemetryConfig:
    enabled: Optional[bool] = None
    listen_address: Optional[str] = None
    listen_port: Optional[int] = None
    path: Optional[str] = None
    metric_prefix: Optional[str] = None
    expose: Optional[TelemetryExpose] = None
    # Price source priority per logical source (e.g., liqwid, minswap)
    # Example: { "liqwid": ["greptime(liqwid)", "liqwid"], "minswap": ["greptime(minswap)", "minswap"] }
    price_source_priority: Optional[dict] = None


@dataclass
class ScheduleConfig:
    interval_minutes: Optional[int] = None


@dataclass
class SafetyFactor:
    c: Optional[float] = None

@dataclass
class DecisionGateConfig:
    enabled: Optional[bool] = None
    # NOTE: 'whitelist' field removed - no longer used in code (residual from earlier version)
    basis: Optional[str] = None  # 'corrected_position' | 'change_rate_usd'
    # Baseline method used to form residuals for gating when applicable
    # 'polynomial_fit' (order given by polynomial_order) or 'median' (flat median line)
    method: Optional[str] = None
    polynomial_order: Optional[int] = None
    k_sigma: Optional[float] = None
    min_points: Optional[int] = None
    exclude_last_for_sigma: Optional[bool] = None
    lookback_hours: Optional[float] = None
    sigma_epsilon: Optional[float] = None
    apply_in_fallback: Optional[bool] = None
    # Thresholding mode: 'stddev' (k*sigma) or 'percentile' (central interval around median)
    threshold_mode: Optional[str] = None
    central_confidence: Optional[float] = None  # central interval mass when threshold_mode='percentile'


@dataclass
class PlotRangeConfig:
    """Visualization-specific date range (decoupled from data sync range)"""
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    mode: str = "inherit"  # "inherit" | "custom" | "relative"
    relative_duration: Optional[str] = None  # e.g., "7d", "30d"

    def __post_init__(self):
        """Validate configuration"""
        # Validate mode
        valid_modes = {"inherit", "custom", "relative"}
        if self.mode not in valid_modes:
            log.warning(f"Invalid plot_range mode '{self.mode}', defaulting to 'inherit'")
            self.mode = "inherit"
        
        # Validate relative_duration format if mode is relative
        if self.mode == "relative" and self.relative_duration is not None:
            import re
            if not re.match(r'^\d+[dhw]$', self.relative_duration.lower().strip()):
                log.warning(f"Invalid relative_duration format '{self.relative_duration}', expected format like '7d', '12h', '1w'")

    def resolve(self, data_range: "DateRange", now: Optional[datetime] = None) -> "DateRange":
        """
        Resolve plot range based on mode:
        - inherit: Use data_range as-is
        - custom: Use start/end if set, else data_range
        - relative: Calculate from now - relative_duration to now
        
        Returns:
            DateRange object representing the resolved plot range
        """
        from datetime import timezone, timedelta
        from ..shared.config import DateRange
        
        if self.mode == "inherit":
            return data_range
        
        elif self.mode == "custom":
            # Use custom start/end if provided, otherwise fall back to data_range
            resolved_start = self.start if self.start is not None else data_range.start
            resolved_end = self.end if self.end is not None else data_range.end
            return DateRange(start=resolved_start, end=resolved_end)
        
        elif self.mode == "relative":
            if self.relative_duration is None:
                # Fall back to inherit if no duration specified
                return data_range
            
            # Parse relative duration and calculate range
            now_dt = now if now is not None else datetime.now(timezone.utc)
            
            # Simple parser for relative duration (e.g., "7d", "30d", "12h", "1w")
            import re
            match = re.match(r'^(\d+)([dhw])$', self.relative_duration.lower().strip())
            if not match:
                # Invalid format, fall back to inherit
                return data_range
            
            value, unit = int(match.group(1)), match.group(2)
            if unit == 'd':
                duration_td = timedelta(days=value)
            elif unit == 'h':
                duration_td = timedelta(hours=value)
            elif unit == 'w':
                duration_td = timedelta(weeks=value)
            else:
                # Unknown unit, fall back to inherit
                return data_range
            
            resolved_start = now_dt - duration_td
            resolved_end = now_dt
            return DateRange(start=resolved_start, end=resolved_end)
        
        else:
            # Unknown mode, fall back to inherit
            return data_range


@dataclass
@dataclass
class AggregationConfig:
    """Data aggregation settings for dashboard performance optimization"""
    enabled: bool = True  # Changed default to True for performance
    method: str = "whiskers"  # "whiskers" | "none"
    time_unit: str = "1d"  # Default time unit (can be any valid interval)
    ui_time_units: Optional[list[str]] = None  # Time units shown in UI dropdown
    percentiles: Optional[list[float]] = None  # For whisker plots, e.g., [10, 25, 50, 75, 90]
    show_raw_points: bool = False  # Show raw data points in addition to whiskers
    
    def __post_init__(self):
        """Set default percentiles and validate configuration"""
        if self.percentiles is None:
            self.percentiles = [10.0, 25.0, 50.0, 75.0, 90.0]
        
        # Set default UI time units (exclude 1min as it's too granular)
        if self.ui_time_units is None:
            self.ui_time_units = ["5min", "15min", "1h", "6h", "1d", "3d", "1w"]
        
        # Validate method
        valid_methods = {"whiskers", "none"}
        if self.method not in valid_methods:
            log.warning(f"Invalid aggregation method '{self.method}', defaulting to 'whiskers'")
            self.method = "whiskers"
        
        # Validate time_unit (expanded to support more intervals)
        valid_time_units = {"1min", "5min", "15min", "30min", "1h", "6h", "12h", "1d", "3d", "1w"}
        if self.time_unit not in valid_time_units:
            log.warning(f"Invalid aggregation time_unit '{self.time_unit}', defaulting to '1d'")
            self.time_unit = "1d"
        
        # Validate ui_time_units (must be subset of valid_time_units)
        if self.ui_time_units:
            invalid_units = [u for u in self.ui_time_units if u not in valid_time_units]
            if invalid_units:
                log.warning(f"Invalid UI time units {invalid_units}, removing them")
                self.ui_time_units = [u for u in self.ui_time_units if u in valid_time_units]
        
        # Validate percentiles (must be between 0 and 100, sorted)
        if self.percentiles:
            valid_percentiles = [p for p in self.percentiles if 0 <= p <= 100]
            if len(valid_percentiles) != len(self.percentiles):
                log.warning(f"Some percentiles out of range [0, 100], filtered to valid values")
                self.percentiles = valid_percentiles
            
            # Sort percentiles
            self.percentiles = sorted(self.percentiles)


@dataclass
class DiagnosticsConfig:
    enabled: Optional[bool] = None
    dir: Optional[str] = None
    include_sigma_band: Optional[bool] = None
    include_k_sigma_band: Optional[bool] = None
    # NOTE: 'only_whitelist_assets' field removed - deprecated and never used
    lookback_hours_override: Optional[float] = None
    hist_samples_per_bin: Optional[int] = None
    # New fields for plot range control and aggregation
    plot_range: Optional[PlotRangeConfig] = None
    aggregation: Optional[AggregationConfig] = None


@dataclass
class OutputCleanupConfig:
    enabled: Optional[bool] = None
    # Expiry cutoff: files strictly older than this timestamp will be deleted
    # Accepts ISO date/time string in YAML. Example: "2025-10-01" or "2025-10-01T00:00:00".
    expire_before: Optional[datetime] = None
    # Relative duration notation (e.g., "7d", "1w", "48h"). If provided, overrides expire_before.
    expire_before_relative: Optional[str] = None
    # Directories (relative to repo root) to scan. Defaults target orchestrator/client outputs.
    paths: Optional[list[str]] = None
    # File extensions to consider. Case-insensitive.
    extensions: Optional[list[str]] = None



# ===== Phase 2: New configuration blocks (added alongside legacy for migration) =====
@dataclass
class PricesV2Config:
    # Logical sources and their provider priority
    # Example: sources=["liqwid","minswap"], priority_by_logical={"liqwid": ["greptime(liqwid)", "liqwid_graphql"], "minswap": ["greptime(minswap)", "minswap_aggregator"]}
    sources: Optional[list[str]] = None
    priority_by_logical: Optional[dict] = None
    duty_cycle_threshold: Optional[float] = None
    # Optional endpoints for providers
    endpoints: Optional[dict] = None


@dataclass
class TrendIndicatorV2:
    enabled: Optional[bool] = None
    method: Optional[str] = None  # polynomial_fit | moving_average
    polynomial_order: Optional[int] = None
    window_size_hours: Optional[float] = None
    window_type: Optional[str] = None  # polynomial | gaussian | boxcar | none
    gaussian_kde_sigma_fraction: Optional[float] = None
    per_asset: Optional[dict] = None


@dataclass
class AnalysisV2Config:
    trend_indicator: Optional[TrendIndicatorV2] = None
    price_compare: Optional["PriceCompareV2"] = None


@dataclass
class PriceCompareV2:
    enabled: Optional[bool] = None
    sources: Optional[list[str]] = None
    epsilon_mode: Optional[str] = None
    tolerance_epsilon: Optional[float] = None
    per_asset_overrides: Optional[dict] = None
    action_on_mismatch: Optional[str] = None
    persistence_threshold: Optional[int] = None
    request_timeout_seconds: Optional[int] = None
    retries: Optional[int] = None
    currency: Optional[str] = None


@dataclass
class TransactionSyncConfig:
    """Configuration for transaction sync from Liqwid API"""
    start_date: Optional[str] = None  # Default sync start date (ISO format: YYYY-MM-DD)
    end_date: Optional[str] = None    # Default sync end date (ISO format: YYYY-MM-DD, null = now)

@dataclass
class OrchestratorConfig:
    reference_keyword: Optional[str] = None
    reference_keyword_fallback: Optional[str] = None  # 'null' or 'data_range'
    safety_factor: Optional[SafetyFactor] = None
    timezone: Optional[str] = None
    schedule: Optional[ScheduleConfig] = None
    telemetry: Optional[TelemetryConfig] = None
    decision_gate: Optional[DecisionGateConfig] = None
    # Diagnostic plots for residual gate
    diagnostics: Optional[DiagnosticsConfig] = None
    # API endpoints used by orchestrator clients
    apis: Optional["ApisConfig"] = None
    # Transaction sync configuration
    transaction_sync: Optional[TransactionSyncConfig] = None
    # Optional wallet address list for transaction sync fallback when discovery yields nothing.
    # Wallet addresses may be configured directly via 'address' or via environment variable via 'address_env'.
    wallets: Optional[List[str]] = None
    # Dashboard auth (dev in-app basic auth)
    auth: Optional["AuthConfig"] = None
    # Output cleanup policy
    cleanup: Optional[OutputCleanupConfig] = None
    # Phase 2 (new) blocks exposed under orchestrator for migration
    prices_v2: Optional[PricesV2Config] = None
    analysis_v2: Optional[AnalysisV2Config] = None



# (Legacy PriceCompareConfig removed in v2-only cleanup)


@dataclass
class ApisConfig:
    liqwid_graphql: Optional[str] = None
    koios: Optional[str] = None
    minswap_aggregator: Optional[str] = None

@dataclass
class AuthConfig:
    enabled: Optional[bool] = None
    user_env: Optional[str] = None
    pass_env: Optional[str] = None

@dataclass
class Settings:
    client: ClientConfig
    orchestrator: OrchestratorConfig
    config_path: Path


class SettingsError(Exception):
    pass


def _validate_config(settings: Settings) -> None:
    """
    Validate all required configuration fields are present and valid.
    Fail-fast with clear error messages at config load time.
    
    Raises:
        SettingsError: If any required field is missing or invalid
    """
    errors = []
    cfg = settings.orchestrator
    
    # ===== Core Settings =====
    if not cfg.timezone or not cfg.timezone.strip():
        errors.append("settings.timezone must be a non-empty string")
    else:
        # Validate timezone is valid
        try:
            if ZoneInfo is not None:
                ZoneInfo(cfg.timezone)
        except Exception:
            errors.append(f"settings.timezone '{cfg.timezone}' is not a valid IANA timezone")
    
    # ===== Reference Settings =====
    if not cfg.reference_keyword or not cfg.reference_keyword.strip():
        errors.append("analysis.decision.reference.keyword must be a non-empty string")
    
    if not cfg.reference_keyword_fallback:
        errors.append("analysis.decision.reference.fallback is required")
    elif cfg.reference_keyword_fallback not in ["null", "data_range"]:
        errors.append("analysis.decision.reference.fallback must be 'null' or 'data_range'")
    
    # ===== Safety Factor =====
    if cfg.safety_factor is None:
        errors.append("analysis.decision.safety_factor is required")
    else:
        if cfg.safety_factor.c is None:
            errors.append("analysis.decision.safety_factor.c is required")
        elif not (0.0 <= cfg.safety_factor.c <= 1.0):
            errors.append(f"analysis.decision.safety_factor.c must be in [0,1], got: {cfg.safety_factor.c}")
    
    # ===== Schedule =====
    if cfg.schedule is None:
        errors.append("runtime.schedule is required")
    else:
        if cfg.schedule.interval_minutes is None:
            errors.append("runtime.schedule.interval_minutes is required")
        elif cfg.schedule.interval_minutes < 1:
            errors.append(f"runtime.schedule.interval_minutes must be >= 1, got: {cfg.schedule.interval_minutes}")
    
    # ===== Telemetry =====
    if cfg.telemetry is None:
        errors.append("runtime.telemetry is required")
    else:
        if cfg.telemetry.enabled is None:
            errors.append("runtime.telemetry.enabled is required")
        
        if cfg.telemetry.enabled:
            if not cfg.telemetry.listen_address or not cfg.telemetry.listen_address.strip():
                errors.append("runtime.telemetry.listen_address required when telemetry enabled")
            
            if cfg.telemetry.listen_port is None:
                errors.append("runtime.telemetry.listen_port required when telemetry enabled")
            elif not (1 <= cfg.telemetry.listen_port <= 65535):
                errors.append(f"runtime.telemetry.listen_port must be in [1,65535], got: {cfg.telemetry.listen_port}")
            
            if not cfg.telemetry.path or not cfg.telemetry.path.strip():
                errors.append("runtime.telemetry.path required when telemetry enabled")
            
            if not cfg.telemetry.metric_prefix or not cfg.telemetry.metric_prefix.strip():
                errors.append("runtime.telemetry.metric_prefix required when telemetry enabled")
            
            if cfg.telemetry.expose is None:
                errors.append("runtime.telemetry.expose is required when telemetry enabled")
    
    # ===== Decision Gate (if enabled) =====
    if cfg.decision_gate and cfg.decision_gate.enabled:
        if cfg.decision_gate.method is None:
            errors.append("analysis.decision.gate.method is required when gate enabled")
        elif cfg.decision_gate.method not in ("polynomial_fit", "median"):
            errors.append(f"analysis.decision.gate.method must be 'polynomial_fit' or 'median', got: {cfg.decision_gate.method}")
        
        if cfg.decision_gate.basis is None:
            errors.append("analysis.decision.gate.basis is required when gate enabled")
        elif cfg.decision_gate.basis not in ("corrected_position", "change_rate_usd"):
            errors.append(f"analysis.decision.gate.basis must be 'corrected_position' or 'change_rate_usd', got: {cfg.decision_gate.basis}")
        
        if cfg.decision_gate.polynomial_order is None:
            errors.append("analysis.decision.gate.polynomial_order is required when gate enabled")
        elif cfg.decision_gate.polynomial_order < 1:
            errors.append(f"analysis.decision.gate.polynomial_order must be >= 1, got: {cfg.decision_gate.polynomial_order}")
        
        if cfg.decision_gate.k_sigma is None:
            errors.append("analysis.decision.gate.k_sigma is required when gate enabled")
        elif cfg.decision_gate.k_sigma <= 0:
            errors.append(f"analysis.decision.gate.k_sigma must be > 0, got: {cfg.decision_gate.k_sigma}")
        
        if cfg.decision_gate.min_points is None:
            errors.append("analysis.decision.gate.min_points is required when gate enabled")
        elif cfg.decision_gate.polynomial_order is not None and cfg.decision_gate.min_points < cfg.decision_gate.polynomial_order + 1:
            errors.append(f"analysis.decision.gate.min_points must be >= polynomial_order + 1, got: {cfg.decision_gate.min_points}")
        
        if cfg.decision_gate.lookback_hours is not None and cfg.decision_gate.lookback_hours <= 0:
            errors.append(f"analysis.decision.gate.lookback_hours must be positive when set, got: {cfg.decision_gate.lookback_hours}")
        
        if cfg.decision_gate.sigma_epsilon is None:
            errors.append("analysis.decision.gate.sigma_epsilon is required when gate enabled")
        elif cfg.decision_gate.sigma_epsilon <= 0:
            errors.append(f"analysis.decision.gate.sigma_epsilon must be > 0, got: {cfg.decision_gate.sigma_epsilon}")
        
        if cfg.decision_gate.threshold_mode is None:
            errors.append("analysis.decision.gate.threshold_mode is required when gate enabled")
        elif cfg.decision_gate.threshold_mode not in ("stddev", "percentile"):
            errors.append(f"analysis.decision.gate.threshold_mode must be 'stddev' or 'percentile', got: {cfg.decision_gate.threshold_mode}")
        
        if cfg.decision_gate.central_confidence is None:
            errors.append("analysis.decision.gate.central_confidence is required when gate enabled")
        elif not (0.0 < cfg.decision_gate.central_confidence < 1.0):
            errors.append(f"analysis.decision.gate.central_confidence must be in (0,1), got: {cfg.decision_gate.central_confidence}")
    
    # ===== Diagnostics (if enabled) =====
    if cfg.diagnostics and cfg.diagnostics.enabled:
        if not cfg.diagnostics.dir or not cfg.diagnostics.dir.strip():
            errors.append("visualization.diagnostics.dir required when diagnostics enabled")
        
        if cfg.diagnostics.hist_samples_per_bin is None:
            errors.append("visualization.diagnostics.hist_samples_per_bin is required when diagnostics enabled")
        elif cfg.diagnostics.hist_samples_per_bin <= 0:
            errors.append(f"visualization.diagnostics.hist_samples_per_bin must be > 0, got: {cfg.diagnostics.hist_samples_per_bin}")
        
        if cfg.diagnostics.lookback_hours_override is not None and cfg.diagnostics.lookback_hours_override <= 0:
            errors.append(f"visualization.diagnostics.lookback_hours_override must be positive when set, got: {cfg.diagnostics.lookback_hours_override}")
    
    # ===== Transaction Sync =====
    if cfg.transaction_sync is None:
        errors.append("prices.transaction_sync is required")
    else:
        # Validate date formats if provided
        if cfg.transaction_sync.start_date is not None:
            try:
                datetime.fromisoformat(cfg.transaction_sync.start_date)
            except ValueError:
                errors.append(f"prices.transaction_sync.start_date must be ISO format (YYYY-MM-DD), got: {cfg.transaction_sync.start_date}")
        
        if cfg.transaction_sync.end_date is not None:
            try:
                datetime.fromisoformat(cfg.transaction_sync.end_date)
            except ValueError:
                errors.append(f"prices.transaction_sync.end_date must be ISO format (YYYY-MM-DD), got: {cfg.transaction_sync.end_date}")
    
    # ===== Prices V2 =====
    if cfg.prices_v2 is None:
        errors.append("prices section is required")
    else:
        if not cfg.prices_v2.sources:
            errors.append("prices.sources must be a non-empty list")
        
        if cfg.prices_v2.duty_cycle_threshold is None:
            errors.append("prices.duty_cycle_threshold is required")
        elif not (0 < cfg.prices_v2.duty_cycle_threshold <= 1):
            errors.append(f"prices.duty_cycle_threshold must be in (0,1], got: {cfg.prices_v2.duty_cycle_threshold}")
    
    # ===== Analysis V2 =====
    if cfg.analysis_v2 is None:
        errors.append("analysis section is required")
    else:
        # Trend Indicator
        if cfg.analysis_v2.trend_indicator and cfg.analysis_v2.trend_indicator.enabled:
            ti = cfg.analysis_v2.trend_indicator
            if ti.method is None:
                errors.append("analysis.trend_indicator.method is required when enabled")
            elif ti.method not in ("polynomial_fit", "moving_average"):
                errors.append(f"analysis.trend_indicator.method must be 'polynomial_fit' or 'moving_average', got: {ti.method}")
            
            if ti.polynomial_order is None:
                errors.append("analysis.trend_indicator.polynomial_order is required when enabled")
            elif ti.polynomial_order < 1:
                errors.append(f"analysis.trend_indicator.polynomial_order must be >= 1, got: {ti.polynomial_order}")
            
            if ti.window_size_hours is None:
                errors.append("analysis.trend_indicator.window_size_hours is required when enabled")
            elif ti.window_size_hours <= 0:
                errors.append(f"analysis.trend_indicator.window_size_hours must be > 0, got: {ti.window_size_hours}")
        
        # Price Compare
        if cfg.analysis_v2.price_compare and cfg.analysis_v2.price_compare.enabled:
            pc = cfg.analysis_v2.price_compare
            if not pc.sources:
                errors.append("analysis.price_compare.sources must be a non-empty list when enabled")
            
            if pc.epsilon_mode is None:
                errors.append("analysis.price_compare.epsilon_mode is required when enabled")
            elif pc.epsilon_mode not in ("relative", "absolute"):
                errors.append(f"analysis.price_compare.epsilon_mode must be 'relative' or 'absolute', got: {pc.epsilon_mode}")
            
            if pc.tolerance_epsilon is None:
                errors.append("analysis.price_compare.tolerance_epsilon is required when enabled")
            elif pc.tolerance_epsilon < 0:
                errors.append(f"analysis.price_compare.tolerance_epsilon must be >= 0, got: {pc.tolerance_epsilon}")
            
            if pc.action_on_mismatch is None:
                errors.append("analysis.price_compare.action_on_mismatch is required when enabled")
            elif pc.action_on_mismatch not in ("hold", "flag_only"):
                errors.append(f"analysis.price_compare.action_on_mismatch must be 'hold' or 'flag_only', got: {pc.action_on_mismatch}")
            
            if pc.persistence_threshold is None:
                errors.append("analysis.price_compare.persistence_threshold is required when enabled")
            elif pc.persistence_threshold < 1:
                errors.append(f"analysis.price_compare.persistence_threshold must be >= 1, got: {pc.persistence_threshold}")
    
    # ===== APIs (if price_compare enabled) =====
    if cfg.analysis_v2 and cfg.analysis_v2.price_compare and cfg.analysis_v2.price_compare.enabled:
        if cfg.analysis_v2.price_compare.sources:
            use_set = {str(s).strip().lower() for s in cfg.analysis_v2.price_compare.sources}
            if cfg.apis:
                if "liqwid" in use_set and not cfg.apis.liqwid_graphql:
                    errors.append("prices.endpoints.liqwid_graphql is required when Liqwid used in analysis.price_compare.sources")
                if "minswap" in use_set and not cfg.apis.minswap_aggregator:
                    errors.append("prices.endpoints.minswap_aggregator is required when Minswap used in analysis.price_compare.sources")
            else:
                errors.append("prices.endpoints are required when analysis.price_compare is enabled")
    
    # ===== Auth (if enabled) =====
    if cfg.auth and cfg.auth.enabled:
        if not cfg.auth.user_env or not cfg.auth.user_env.strip():
            errors.append("runtime.auth.user_env required when auth enabled")
        if not cfg.auth.pass_env or not cfg.auth.pass_env.strip():
            errors.append("runtime.auth.pass_env required when auth enabled")

    # ===== Wallets (optional) =====
    if getattr(cfg, 'wallets', None) is not None:
        if not isinstance(cfg.wallets, list) or not cfg.wallets:
            errors.append("wallets must be a non-empty list when provided")
        else:
            for i, w in enumerate(cfg.wallets):
                if not isinstance(w, str) or not w.strip():
                    errors.append(f"wallets[{i}] must be a non-empty string")
                    continue
                addr = str(w).strip()
                if not addr.startswith(('addr1', 'addr_test1')):
                    errors.append(f"wallets[{i}] invalid wallet address format: {addr}")
     
    # ===== Cleanup (if enabled) =====
    if cfg.cleanup and cfg.cleanup.enabled:
        if not cfg.cleanup.paths:
            errors.append("maintenance.cleanup.paths must be a non-empty list when cleanup enabled")
        if not cfg.cleanup.extensions:
            errors.append("maintenance.cleanup.extensions must be a non-empty list when cleanup enabled")
    
    # ===== Client Config =====
    client = settings.client
    if not client.assets:
        errors.append("domain.assets must be a non-empty list")
    
    if client.greptime is None:
        errors.append("data.databases.greptime is required")
    else:
        if not client.greptime.host or not client.greptime.host.strip():
            errors.append("data.databases.greptime.host is required")
        if client.greptime.port is None or not (1 <= client.greptime.port <= 65535):
            errors.append(f"data.databases.greptime.port must be in [1,65535], got: {client.greptime.port}")
        if not client.greptime.database or not client.greptime.database.strip():
            errors.append("data.databases.greptime.database is required")
    
    # Raise all errors at once for better UX
    if errors:
        error_msg = f"Configuration validation failed ({settings.config_path}):\n" + "\n".join(f"  • {e}" for e in errors)
        raise SettingsError(error_msg)


def load_settings(config_path: str | Path) -> Settings:
    path = Path(config_path)
    if not path.exists():
        raise SettingsError(f"Configuration file not found: {config_path}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise SettingsError(f"Invalid YAML in {config_path}: {e}")

    # Detect new schema revamp (top-level sections)
    is_new_schema = any(k in (raw.keys() if isinstance(raw, dict) else []) for k in (
        "settings", "domain", "data", "prices", "analysis", "runtime", "visualization", "maintenance"
    ))

    if is_new_schema:
        settings = _load_settings_new_schema(raw, path)
    else:
        raise SettingsError("Legacy schema not supported. Use v2 sections: settings, domain, data, prices, analysis, runtime, visualization, maintenance.")

    # ===== Preflight: verify configured sources DB reachability =====
    try:
        from ..shared.greptime_reader import GreptimeReader
        import copy
        ok_all = True
        # Derive greptime-backed providers from v2 prices
        provs = []
        try:
            prio = dict(getattr(settings.orchestrator.prices_v2, 'priority_by_logical', {}) or {})
            seen = set()
            for v in prio.values():
                for prov in (v or []):
                    ps = str(prov).strip()
                    if ps.lower().startswith("greptime(") and ps not in seen:
                        provs.append(ps); seen.add(ps)
        except Exception:
            pass
        if not provs:
            provs = ["greptime(liqwid)"]
        seen = set()
        for s in provs:
            if s in seen:
                continue
            seen.add(s)
            if s.startswith("greptime(") and s.endswith(")"):
                dbname = s[len("greptime("):-1]
                gcfg = copy.deepcopy(settings.client.greptime)
                gcfg.database = dbname
                reader = GreptimeReader(gcfg, settings.client.table_asset_prefix)
                if not reader.test_connection():
                    ok_all = False
            else:
                ok_all = False
        if "greptime(liqwid)" not in {str(x).strip().lower() for x in provs}:
            ok_all = False
        if not ok_all:
            raise SettingsError("Preflight failed: verify Greptime sources are reachable and include greptime(liqwid)")
    except Exception as e:
        raise SettingsError(str(e))

    # ===== Timezone hardening for date_range and cleanup.expire_before =====
    # Interpret naive client.date_range datetimes in orchestrator.timezone, then convert to UTC
    try:
        dr = settings.client.date_range
        tz_name = getattr(settings.orchestrator, 'timezone', 'UTC') or 'UTC'
        tz = ZoneInfo(tz_name) if ZoneInfo is not None else timezone.utc

        def _to_utc(dt: Optional[datetime]) -> Optional[datetime]:
            if dt is None:
                return None
            # If naive, assume it's in orchestrator timezone; if aware, convert to that tz first
            if dt.tzinfo is None:
                local = dt.replace(tzinfo=tz)
            else:
                local = dt.astimezone(tz)
            return local.astimezone(timezone.utc)

        if dr is not None:
            dr.start = _to_utc(dr.start)
            dr.end = _to_utc(dr.end)
        # Resolve cleanup expire_before: support relative durations and convert to UTC
        try:
            cl = settings.orchestrator.cleanup
            if isinstance(cl, OutputCleanupConfig):
                # If relative is provided, compute cutoff as now - delta
                rel = getattr(cl, 'expire_before_relative', None)
                def _parse_rel(s: str) -> timedelta:
                    s = s.strip().lower()
                    import re as _re
                    m = _re.match(r"^(\d+(?:\.\d+)?)([smhdw])$", s)
                    if not m:
                        raise ValueError("Invalid relative duration; expected formats like '7d', '1w', '24h'")
                    val = float(m.group(1)); unit = m.group(2)
                    if unit == 's':
                        return timedelta(seconds=val)
                    if unit == 'm':
                        return timedelta(minutes=val)
                    if unit == 'h':
                        return timedelta(hours=val)
                    if unit == 'd':
                        return timedelta(days=val)
                    if unit == 'w':
                        return timedelta(weeks=val)
                    raise ValueError("Unsupported duration unit")
                if rel and cl is not None:
                    now_local = datetime.now(tz)
                    delta = _parse_rel(rel)
                    cutoff_local = now_local - delta
                    cl.expire_before = cutoff_local.astimezone(timezone.utc)
                elif cl is not None and cl.expire_before is not None:
                    dt = cl.expire_before
                    if dt.tzinfo is None:
                        local = dt.replace(tzinfo=tz)
                    else:
                        local = dt.astimezone(tz)
                    cl.expire_before = local.astimezone(timezone.utc)
        except Exception:
            pass
    except Exception:
        # Non-fatal: keep original if conversion fails
        pass

    # NOTE: Whitelist logic removed - decision_gate applies to all configured assets
    #       No need for whitelist-vs-smoothing conflict checks anymore

    return settings


def _load_settings_new_schema(raw: dict, path: Path) -> Settings:
    try:
        # ----- settings & domain -----
        settings_root = raw.get("settings", {}) or {}
        domain = raw.get("domain", {}) or {}
        timezone_val = str(settings_root.get("timezone", "Asia/Tokyo"))
        currency_val = str(settings_root.get("currency", "usd")).strip().lower()
        assets = [str(a).strip().lower() for a in (domain.get("assets", []) or [])]

        # ----- wallets (optional; used as fallback for transaction sync) -----
        wallets_cfg: Optional[List[str]] = None
        if "wallets" in raw:
            wallets_raw = raw.get("wallets")
            if wallets_raw is None:
                wallets_cfg = None
            elif not isinstance(wallets_raw, list):
                raise SettingsError("wallets must be a list")
            elif len(wallets_raw) == 0:
                raise SettingsError("wallets must be a non-empty list when provided")
            else:
                resolved_wallets: List[str] = []
                for i, item in enumerate(wallets_raw):
                    if isinstance(item, str):
                        addr = item.strip()
                        if not addr:
                            raise SettingsError(f"wallets[{i}] must be a non-empty string")
                        if not addr.startswith(('addr1', 'addr_test1')):
                            raise SettingsError(f"wallets[{i}] invalid wallet address format: {addr}")
                        resolved_wallets.append(addr)
                        continue

                    if not isinstance(item, dict):
                        raise SettingsError(f"wallets[{i}] must be a string or mapping")

                    env_key = str(item.get("address_env") or "").strip()
                    addr_val: Optional[str] = None
                    if env_key:
                        env_val = os.getenv(env_key)
                        if env_val is None or not str(env_val).strip():
                            raise SettingsError(
                                f"Wallet address environment variable not set: {env_key} (wallets[{i}])"
                            )
                        addr_val = str(env_val).strip()
                    else:
                        raw_addr = item.get("address")
                        if raw_addr is not None:
                            addr_val = str(raw_addr).strip()

                    if addr_val is None or not addr_val.strip():
                        raise SettingsError(f"wallets[{i}] must set 'address' or 'address_env'")
                    if not addr_val.startswith(('addr1', 'addr_test1')):
                        raise SettingsError(f"wallets[{i}] invalid wallet address format: {addr_val}")
                    resolved_wallets.append(addr_val)

                # De-duplicate while preserving order
                seen_wallets: set[str] = set()
                deduped_wallets: List[str] = []
                for addr in resolved_wallets:
                    if addr not in seen_wallets:
                        seen_wallets.add(addr)
                        deduped_wallets.append(addr)
                wallets_cfg = deduped_wallets

        # ----- data (databases, datasets, date_range) -----
        data = raw.get("data", {}) or {}
        dbs = data.get("databases", {}) or {}
        g_raw = dbs.get("greptime", {}) or {}
        greptime_cfg = GreptimeConnConfig(
            host=str(g_raw.get("host", "http://localhost")),
            port=int(g_raw.get("port", 4000)),
            database=str(g_raw.get("database", "liqwid")),
            timeout=int(g_raw.get("timeout", 10)),
        )
        # date range
        dr_raw = data.get("date_range", {}) or {}
        def _parse_dt(v):
            if v is None:
                return None
            try:
                return datetime.fromisoformat(str(v))
            except Exception:
                return None
        date_range = DateRange(start=_parse_dt(dr_raw.get("start")), end=_parse_dt(dr_raw.get("end")))
        # datasets.transactions
        datasets = data.get("datasets", {}) or {}
        tx_root = (datasets.get("transactions", {}) or {})
        align = str(tx_root.get("alignment_method", "none"))
        tx_sources = tx_root.get("sources", {}) or {}
        liqwid_src = tx_sources.get("liqwid", {}) or {}
        table_asset_prefix = str(liqwid_src.get("table_asset_prefix", "liqwid_supply_positions_"))
        deposits_prefix = str(liqwid_src.get("deposits_prefix", "liqwid_deposits_"))
        withdrawals_prefix = str(liqwid_src.get("withdrawals_prefix", "liqwid_withdrawals_"))

        # Minimal output config
        output_cfg = OutputConfig()
        client_cfg = ClientConfig(
            greptime=greptime_cfg,
            assets=assets,
            table_asset_prefix=table_asset_prefix,
            deposits_prefix=deposits_prefix,
            withdrawals_prefix=withdrawals_prefix,
            alignment_method=align,
            date_range=date_range,
            output=output_cfg,
            tx_timestamp_source="timestamp",
        )

        # ----- prices (v2) -----
        prices_root = raw.get("prices", {}) or {}
        p_sources = [str(s).strip().lower() for s in (prices_root.get("sources", []) or []) if str(s).strip()]
        p_prio_raw = prices_root.get("priority_by_logical", {}) or {}
        p_prio = {}
        if isinstance(p_prio_raw, dict):
            for k, v in p_prio_raw.items():
                if isinstance(v, list):
                    p_prio[str(k).strip().lower()] = [str(x).strip() for x in v if str(x).strip()]
        p_duty = float(prices_root.get("duty_cycle_threshold", 0.9))
        if not (0 < p_duty <= 1):
            p_duty = 0.9
        p_endpoints = prices_root.get("endpoints", {}) or {}
        prices_v2 = PricesV2Config(
            sources=p_sources,
            priority_by_logical=p_prio,
            duty_cycle_threshold=p_duty,
            endpoints=p_endpoints,
        )

        # Derive greptime-backed provider list for preflight
        greptime_providers: list[str] = []
        try:
            seen = set()
            for v in p_prio.values():
                for prov in v or []:
                    ps = str(prov).strip()
                    if ps.lower().startswith("greptime(") and ps not in seen:
                        greptime_providers.append(ps); seen.add(ps)
        except Exception:
            pass
        if not greptime_providers:
            greptime_providers = ["greptime(liqwid)"]

        # ----- analysis (v2) -----
        analysis_root = raw.get("analysis", {}) or {}
        # trend indicator
        ti_raw = analysis_root.get("trend_indicator", {}) or {}
        ti = TrendIndicatorV2(
            enabled=bool(ti_raw.get("enabled", True)),
            method=str(ti_raw.get("method", "polynomial_fit")).strip().lower(),
            polynomial_order=int(ti_raw.get("polynomial_order", 2)),
            window_size_hours=float(ti_raw.get("window_size_hours", 24.0)),
            window_type=str(ti_raw.get("window_type", "polynomial")).strip().lower(),
            gaussian_kde_sigma_fraction=float(ti_raw.get("gaussian_kde_sigma_fraction", 0.3)),
            per_asset=dict(ti_raw.get("per_asset", {}) or {}),
        )
        # decision
        decision_root = analysis_root.get("decision", {}) or {}
        ref_root = decision_root.get("reference", {}) or {}
        keyword = str(ref_root.get("keyword", "alert_driven_withdrawal"))
        fallback = str(ref_root.get("fallback", "null")).strip().lower()
        if fallback not in ("null", "data_range"):
            fallback = "null"
        gate_root = decision_root.get("gate", {}) or {}
        _lbh_raw = gate_root.get("lookback_hours")
        _lbh = None
        if _lbh_raw is not None:
            try:
                _lbh = float(_lbh_raw)
            except Exception:
                _lbh = None
        decision_gate = DecisionGateConfig(
            enabled=bool(gate_root.get("enabled", False)),
            basis=str(gate_root.get("basis", "corrected_position")).strip().lower(),
            method=str(gate_root.get("method", "polynomial_fit")).strip().lower() if str(gate_root.get("method", "median")).strip().lower() in ("polynomial_fit", "median") else "median",
            polynomial_order=int(gate_root.get("polynomial_order", 2 if str(gate_root.get("method", "median")).strip().lower() == "polynomial_fit" else 1)),
            k_sigma=float(gate_root.get("k_sigma", 2.0)),
            min_points=int(gate_root.get("min_points", 20)),
            exclude_last_for_sigma=bool(gate_root.get("exclude_last_for_sigma", True)),
            lookback_hours=_lbh,
            sigma_epsilon=float(gate_root.get("sigma_epsilon", 1e-6)),
            threshold_mode=str(gate_root.get("threshold_mode", "stddev")).strip().lower(),
            central_confidence=float(gate_root.get("central_confidence", 0.68)),
        )
        sf_root = decision_root.get("safety_factor", {}) or {}
        safety = SafetyFactor(c=float(sf_root.get("c", 0.5)))

        # price_compare v2
        pc2_root = analysis_root.get("price_compare", {}) or {}
        pc_sources = pc2_root.get("sources", [])
        if isinstance(pc_sources, str) and pc_sources.strip() == "@prices.sources":
            pc_sources_list = list(p_sources)
        else:
            pc_sources_list = [str(s).strip().lower() for s in (pc_sources or []) if str(s).strip()]
        pc2 = PriceCompareV2(
            enabled=bool(pc2_root.get("enabled", False)),
            sources=pc_sources_list,
            epsilon_mode=str(pc2_root.get("epsilon_mode", "relative")).strip().lower(),
            tolerance_epsilon=float(pc2_root.get("tolerance_epsilon", 0.01)),
            per_asset_overrides=dict(pc2_root.get("per_asset_overrides", {}) or {}),
            action_on_mismatch=str(pc2_root.get("action_on_mismatch", "hold")).strip().lower(),
            persistence_threshold=int(pc2_root.get("persistence_threshold", 1)),
            request_timeout_seconds=int(pc2_root.get("request_timeout_seconds", 5)),
            retries=int(pc2_root.get("retries", 1)),
            currency=currency_val or str(pc2_root.get("currency", "usd")).strip().lower(),
        )
        analysis_v2 = AnalysisV2Config(trend_indicator=ti, price_compare=pc2)

        # ----- runtime -----
        runtime = raw.get("runtime", {}) or {}
        auth_raw = runtime.get("auth", {}) or {}
        auth_cfg = AuthConfig(
            enabled=bool(auth_raw.get("enabled", False)),
            user_env=str(auth_raw.get("user_env", "WO_BASIC_AUTH_USER")),
            pass_env=str(auth_raw.get("pass_env", "WO_BASIC_AUTH_PASS")),
        )
        sched_raw = runtime.get("schedule", {}) or {}
        schedule = ScheduleConfig(interval_minutes=int(sched_raw.get("interval_minutes", 60)))
        tel_raw = runtime.get("telemetry", {}) or {}
        exp_raw = tel_raw.get("expose", {}) or {}
        expose = TelemetryExpose(
            decision=bool(exp_raw.get("decision", True)),
            wmax_usd=bool(exp_raw.get("wmax_usd", True)),
            v_ref_usd=bool(exp_raw.get("v_ref_usd", False)),
            v_t1_usd=bool(exp_raw.get("v_t1_usd", False)),
            g_usd=bool(exp_raw.get("g_usd", False)),
            price_t1_usd=bool(exp_raw.get("price_t1_usd", False)),
            t0_timestamp_seconds=bool(exp_raw.get("t0_timestamp_seconds", False)),
            t1_timestamp_seconds=bool(exp_raw.get("t1_timestamp_seconds", False)),
            residual_usd=bool(exp_raw.get("residual_usd", False)),
            sigma_usd=bool(exp_raw.get("sigma_usd", False)),
            k_sigma=bool(exp_raw.get("k_sigma", False)),
            residual_trigger=bool(exp_raw.get("residual_trigger", False)),
            price_usd_by_source=bool(exp_raw.get("price_usd_by_source", False)),
            price_delta_abs=bool(exp_raw.get("price_delta_abs", False)),
            price_delta_rel=bool(exp_raw.get("price_delta_rel", False)),
            price_mismatch=bool(exp_raw.get("price_mismatch", False)),
            price_compare_unavailable=bool(exp_raw.get("price_compare_unavailable", False)),
            rate_usd=bool(exp_raw.get("rate_usd", True)),
            rate_ada=bool(exp_raw.get("rate_ada", True)),
        )
        telemetry = TelemetryConfig(
            enabled=bool(tel_raw.get("enabled", True)),
            listen_address=str(tel_raw.get("listen_address", "0.0.0.0")),
            listen_port=int(tel_raw.get("listen_port", 9808)),
            path=str(tel_raw.get("path", "/metrics")),
            metric_prefix=str(tel_raw.get("metric_prefix", "wo_")),
            expose=expose,
            price_source_priority=p_prio,
        )

        # ----- visualization & maintenance -----
        viz = raw.get("visualization", {}) or {}
        diag_raw = (viz.get("diagnostics", {}) or {})
        _lho_raw = diag_raw.get("lookback_hours_override")
        _lho = None
        if _lho_raw is not None:
            try:
                _lho = float(_lho_raw)
            except Exception:
                _lho = None
        
        # Helper for parsing datetime strings (for plot_range)
        def _parse_datetime_viz(v):
            if v is None:
                return None
            try:
                return datetime.fromisoformat(str(v))
            except Exception:
                return None
        
        # Parse plot_range config
        plot_range_raw = diag_raw.get("plot_range", {}) or {}
        plot_range = PlotRangeConfig(
            start=_parse_datetime_viz(plot_range_raw.get("start")),
            end=_parse_datetime_viz(plot_range_raw.get("end")),
            mode=str(plot_range_raw.get("mode", "inherit")).strip().lower(),
            relative_duration=plot_range_raw.get("relative_duration"),
        )
        
        # Parse aggregation config
        agg_raw = diag_raw.get("aggregation", {}) or {}
        ui_time_units_raw = agg_raw.get("ui_time_units")
        aggregation = AggregationConfig(
            enabled=bool(agg_raw.get("enabled", True)),
            method=str(agg_raw.get("method", "whiskers")).strip().lower(),
            time_unit=str(agg_raw.get("time_unit", "1d")).strip().lower(),
            ui_time_units=[str(u).strip().lower() for u in ui_time_units_raw] if ui_time_units_raw else None,
            percentiles=[float(p) for p in (agg_raw.get("percentiles") or [10, 25, 50, 75, 90])],
            show_raw_points=bool(agg_raw.get("show_raw_points", False)),
        )
        
        diagnostics = DiagnosticsConfig(
            enabled=bool(diag_raw.get("enabled", False)),
            dir=str(diag_raw.get("dir")) if diag_raw.get("dir") is not None else None,
            include_sigma_band=bool(diag_raw.get("include_sigma_band", True)),
            include_k_sigma_band=bool(diag_raw.get("include_k_sigma_band", True)),
            # lookback_hours_override may be null
            lookback_hours_override=_lho,
            hist_samples_per_bin=int(diag_raw.get("hist_samples_per_bin", 10)),
            plot_range=plot_range,
            aggregation=aggregation,
        )
        maint = raw.get("maintenance", {}) or {}
        cleanup_root = (maint.get("cleanup", {}) or {})
        # Pass-through: store relative notation in expire_before_relative
        cleanup_cfg = OutputCleanupConfig(
            enabled=bool(cleanup_root.get("enabled", False)),
            expire_before=None,
            expire_before_relative=str(cleanup_root.get("expire_before", "")).strip() or None,
            paths=[str(p) for p in (cleanup_root.get("paths") or [])] if "paths" in cleanup_root else None,
            extensions=[str(x).lower() for x in (cleanup_root.get("extensions") or [])] if "extensions" in cleanup_root else None,
        )

        # Transaction sync config from prices.transaction_sync
        tx_sync_raw = prices_root.get("transaction_sync", {}) or {}
        transaction_sync = TransactionSyncConfig(
            start_date=tx_sync_raw.get("start_date"),
            end_date=tx_sync_raw.get("end_date")
        )

        # OrchestratorConfig (v2-only)
        orchestrator_cfg = OrchestratorConfig(
            reference_keyword=keyword,
            reference_keyword_fallback=fallback,
            safety_factor=safety,
            timezone=timezone_val,
            schedule=schedule,
            telemetry=telemetry,
            decision_gate=decision_gate,
            diagnostics=diagnostics,
            transaction_sync=transaction_sync,
            wallets=wallets_cfg,
            apis=ApisConfig(
                liqwid_graphql=str(p_endpoints.get("liqwid_graphql")) if p_endpoints.get("liqwid_graphql") else None,
                koios=str(p_endpoints.get("koios")) if p_endpoints.get("koios") else None,
                minswap_aggregator=str(p_endpoints.get("minswap_aggregator")) if p_endpoints.get("minswap_aggregator") else None,
            ),
            auth=auth_cfg,
            cleanup=cleanup_cfg,
            prices_v2=prices_v2,
            analysis_v2=analysis_v2,
        )

        settings = Settings(client=client_cfg, orchestrator=orchestrator_cfg, config_path=path)
        
        # ===== Validate all configuration values =====
        _validate_config(settings)
        
        return settings
    except Exception as e:
        raise SettingsError(f"Failed to load new schema: {e}")
