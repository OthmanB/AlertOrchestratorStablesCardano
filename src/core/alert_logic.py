#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Alert logic core (Phase B)

Computes per-asset W_{i,max,usd} using shared Greptime reader for positions,
Liqwid GraphQL prices, smoothing configuration, and reference state. Returns a
structure suitable for console output and metrics emission.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, List
from datetime import datetime, timezone, timedelta

import logging
import numpy as np
from ..shared.config import ClientConfig, DateRange
from ..shared.greptime_reader import GreptimeReader
from ..shared.models import AssetTimeSeries, WalletBreakdown
from ..shared.correct_calculations import calculate_correct_gains
from ..shared.resolver import Resolver
from ..shared.utils import canonicalize_minswap_asset
from .settings import Settings
from .price_source import MinswapAggregatorPriceSource, LiqwidGraphQLPriceSource
from .token_registry import load_registry
from .reference_state import get_last_reference
from .diagnostics import plot_residual_composite
from .debug_dump import dump_asset_debug


@dataclass
class AssetDecision:
    decision: int  # 1=WITHDRAW_OK, 0=HOLD, -1=ERROR
    wmax_usd: List[WalletBreakdown] = field(default_factory=list)
    qmax_units: Optional[float] = None
    v_ref_usd: Optional[float] = None
    v_t1_usd: Optional[float] = None
    g_usd: Optional[float] = None
    price_t1_usd: Optional[float] = None
    # New telemetry/context fields
    ref_mode: Optional[str] = None  # 'keyword', 'data_range', 'null'
    t0_timestamp_seconds: Optional[float] = None
    t1_timestamp_seconds: Optional[float] = None
    # Residual gating context
    residual_usd: Optional[float] = None
    sigma_usd: Optional[float] = None
    k_sigma: Optional[float] = None
    residual_trigger: Optional[int] = None
    gate_applied: Optional[bool] = None
    error: Optional[str] = None
    # Diagnostics
    debug_plot_path: Optional[str] = None
    # Phase C: price compare telemetry
    prices_by_source: Dict[str, float] = field(default_factory=dict)
    price_delta_abs: Optional[float] = None
    price_delta_rel: Optional[float] = None
    price_mismatch: Optional[int] = None  # 0/1
    price_compare_unavailable: Optional[int] = None  # 0/1


def _calculate_per_wallet_wmax(
    reader: GreptimeReader,
    cfg: ClientConfig,
    resolved_symbol: str,
    date_range: DateRange,
    safety_factor_c: float,
    log: logging.Logger
) -> List[WalletBreakdown]:
    """
    Calculate per-wallet Wmax breakdown for an asset.
    
    This helper encapsulates the per-wallet logic:
    1. Fetch per-wallet position series
    2. For each wallet, filter transactions and calculate gains
    3. Compute wmax = max(0, c * gains_wallet)
    4. Return list of WalletBreakdown objects
    
    Args:
        reader: GreptimeReader instance
        cfg: Client configuration
        resolved_symbol: Resolved asset symbol (e.g., "wanusdc")
        date_range: Date range for data fetching
        safety_factor_c: Safety factor coefficient
        log: Logger instance
    
    Returns:
        List of WalletBreakdown objects (one per wallet)
    """
    # Fetch per-wallet position series
    wallet_series_dict = reader.fetch_asset_series_by_wallet(resolved_symbol, date_range)
    
    if not wallet_series_dict:
        log.debug(f"No wallet series data for {resolved_symbol}")
        return []
    
    # Fetch all transactions for this asset (we'll filter per wallet)
    all_txs = reader.fetch_transactions(
        asset_symbol=resolved_symbol,
        deposits_prefix=cfg.deposits_prefix,
        withdrawals_prefix=cfg.withdrawals_prefix,
        date_range=date_range,
    )
    
    wallet_breakdown: List[WalletBreakdown] = []
    
    for wallet_addr, wallet_series in wallet_series_dict.items():
        if not wallet_series.series:
            continue
        
        # Filter transactions for this specific wallet
        wallet_txs = [tx for tx in all_txs if tx.wallet_address == wallet_addr]
        
        # Calculate gains for this wallet
        pos_ts = sorted(wallet_series.series.keys())
        pos_vals = [float(wallet_series.series[ts]) for ts in pos_ts]
        
        try:
            _, _, _, _, gains = calculate_correct_gains(
                position_timestamps=pos_ts,
                position_values=pos_vals,
                transactions=wallet_txs,
                reference_time_index=0,
                interpolation_method="linear",
                alignment_method=str(cfg.alignment_method),
                tx_timestamp_source=str(getattr(cfg, 'tx_timestamp_source', 'timestamp')),
            )
            
            # Calculate Wmax for this wallet
            g_wallet = float(gains[-1]) if len(gains) > 0 else 0.0
            wmax_wallet = max(0.0, safety_factor_c * g_wallet)

            v_t1_usd = float(pos_vals[-1]) if len(pos_vals) > 0 else 0.0
            
            wallet_breakdown.append(WalletBreakdown(
                wallet_address=wallet_addr,
                wmax_usd=wmax_wallet,
                v_t1_usd=v_t1_usd,
            ))
            
            log.debug(
                f"Wallet {wallet_addr[:11]}...{wallet_addr[-6:]}: "
                f"gains={g_wallet:.2f}, wmax={wmax_wallet:.2f}"
            )
            
        except Exception as e:
            log.warning(f"Failed to calculate gains for wallet {wallet_addr}: {e}")
            continue
    
    return wallet_breakdown


def evaluate_once(reader: GreptimeReader, settings: Settings) -> Dict[str, AssetDecision]:
    """Compute per-asset decisions once using current time and configuration.

    This returns a dict mapping asset -> AssetDecision for the metrics layer and
    console summary. It does not perform any writes.
    """
    cfg: ClientConfig = settings.client
    assets = list(cfg.assets or [])
    if not assets:
        return {"_all_": AssetDecision(decision=-1, error="assets list is empty")}

    # Resolve provided assets to actual Greptime symbols (e.g., wanusdc)
    resolver = Resolver(greptime_reader=reader)
    display_to_resolved: Dict[str, str] = {}
    resolution_errors: Dict[str, str] = {}
    for a in assets:
        try:
            _mid, sym = resolver.resolve_asset(a)
            display_to_resolved[a] = sym
        except Exception as e:
            resolution_errors[a] = str(e)

    # Build list of resolvable assets; keep others as errors
    resolvable_assets = [a for a in assets if a in display_to_resolved]

    # Prepare decision-phase price sources (latest price annotation/telemetry)
    apis = getattr(settings.orchestrator, "apis", None)
    # v2-only: pull optional runtime params for decision-time API sources from analysis.price_compare
    _pc2 = getattr(getattr(settings.orchestrator, 'analysis_v2', None), 'price_compare', None)
    _pc_currency = str(getattr(_pc2, 'currency', 'usd'))
    _pc_timeout = int(getattr(_pc2, 'request_timeout_seconds', 5))
    _pc_retries = int(getattr(_pc2, 'retries', 1))
    # Token registry for Minswap source (if available)
    decision_registry = None
    try:
        reg_path0 = settings.config_path.parent / "token_registry.csv"
        decision_registry = load_registry(str(reg_path0))
    except Exception:
        decision_registry = None
    ps_decision_liqwid = None
    ps_decision_minswap = None
    try:
        if apis and getattr(apis, "liqwid_graphql", None):
            ps_decision_liqwid = LiqwidGraphQLPriceSource(endpoint=str(apis.liqwid_graphql), timeout_s=10)
    except Exception:
        ps_decision_liqwid = None
    try:
        if apis and getattr(apis, "minswap_aggregator", None) and decision_registry is not None:
            ps_decision_minswap = MinswapAggregatorPriceSource(
                base_url=str(apis.minswap_aggregator),
                currency=_pc_currency,
                timeout_s=_pc_timeout,
                retries=_pc_retries,
                registry=decision_registry,
            )
    except Exception:
        ps_decision_minswap = None

    # Get last references (t0, V_i(t0)) per RESOLVED asset via withdrawals tagged with keyword
    resolved_symbols = [display_to_resolved[a] for a in resolvable_assets]
    refs_by_resolved = get_last_reference(reader, cfg, resolved_symbols, settings.orchestrator.reference_keyword)

    # Evaluation time (UTC now). Timezone-aware rendering handled upstream if needed.
    now_utc = datetime.now(timezone.utc)
    decisions: Dict[str, AssetDecision] = {}
    log = logging.getLogger(__name__)

    for asset in assets:
        dec = AssetDecision(decision=0)
        try:
            # If resolution failed for this asset, mark error
            if asset not in display_to_resolved:
                dec.decision = -1
                dec.error = resolution_errors.get(asset, "asset resolution failed")
                decisions[asset] = dec
                continue

            resolved = display_to_resolved[asset]
            # Reference point
            t0_v = refs_by_resolved.get(resolved)
            if not t0_v:
                # No tagged withdrawal found -> warn and apply fallback if configured
                fb = (settings.orchestrator.reference_keyword_fallback or "null").lower()
                log.warning(
                    f"No tagged withdrawal found for asset={asset} (resolved={resolved}); "
                    f"fallback={fb}"
                )
                if fb == "data_range" and cfg.date_range and cfg.date_range.start:
                    # Use start of configured data range as t0
                    t0_fallback = cfg.date_range.start
                    if t0_fallback.tzinfo is None:
                        t0_fallback = t0_fallback.replace(tzinfo=timezone.utc)
                    per_range = DateRange(start=t0_fallback, end=now_utc)
                    series_obj0: Optional[AssetTimeSeries] = reader.fetch_asset_series(resolved, per_range)
                    if not series_obj0 or not series_obj0.series:
                        dec.decision = 0
                        dec.v_ref_usd = None
                        dec.g_usd = 0.0
                        dec.wmax_usd = []  # No wallet breakdown for HOLD state
                        dec.ref_mode = "data_range"
                        try:
                            dec.t0_timestamp_seconds = float(t0_fallback.timestamp())
                        except Exception:
                            pass
                        decisions[asset] = dec
                        continue
                    # Set v_ref to first sample in range; v_t1 to last
                    first_ts = min(series_obj0.series.keys())
                    last_ts = max(series_obj0.series.keys())
                    dec.v_ref_usd = float(series_obj0.series[first_ts])
                    dec.v_t1_usd = float(series_obj0.series[last_ts])
                    dec.ref_mode = "data_range"
                    try:
                        dec.t0_timestamp_seconds = float(t0_fallback.timestamp())
                        dec.t1_timestamp_seconds = float(last_ts.timestamp())
                    except Exception:
                        pass
                    
                    # Calculate per-wallet Wmax breakdown
                    dec.wmax_usd = _calculate_per_wallet_wmax(
                        reader=reader,
                        cfg=cfg,
                        resolved_symbol=resolved,
                        date_range=per_range,
                        safety_factor_c=settings.orchestrator.safety_factor.c,
                        log=log
                    )
                    
                    # Compute total gains for gate logic and telemetry
                    # (Keep aggregated calculation for compatibility with residual gate)
                    txs = reader.fetch_transactions(
                        asset_symbol=resolved,
                        deposits_prefix=cfg.deposits_prefix,
                        withdrawals_prefix=cfg.withdrawals_prefix,
                        date_range=per_range,
                    )
                    pos_ts = sorted(series_obj0.series.keys())
                    pos_vals = [float(series_obj0.series[ts]) for ts in pos_ts]
                    timebase, _, _, _, gains = calculate_correct_gains(
                        position_timestamps=pos_ts,
                        position_values=pos_vals,
                        transactions=txs,
                        reference_time_index=0,
                        interpolation_method="linear",
                        alignment_method=str(cfg.alignment_method),
                    )
                    g_i = float(gains[-1]) if len(gains) > 0 else 0.0
                    dec.g_usd = g_i

                    try:
                        diag_cfg = getattr(settings.orchestrator, 'diagnostics', None)
                        if (
                            str(asset).strip().lower() == "usdt"
                            and diag_cfg is not None
                            and bool(getattr(diag_cfg, 'enabled', False))
                            and getattr(diag_cfg, 'dir', None)
                        ):
                            dump_asset_debug(
                                asset_display=asset,
                                resolved_symbol=resolved,
                                reader=reader,
                                cfg=cfg,
                                out_dir=str(getattr(diag_cfg, 'dir')),
                                alignment_method=str(cfg.alignment_method),
                                tx_timestamp_source=str(getattr(cfg, 'tx_timestamp_source', 'timestamp')),
                            )
                    except Exception:
                        pass

                    # Residual gating in fallback if enabled
                    dg = settings.orchestrator.decision_gate
                    apply_gate = bool(dg.enabled)
                    if apply_gate and bool(dg.apply_in_fallback):
                        try:
                            # Build corrected positions on original position timestamps
                            P_t0 = float(dec.v_ref_usd) if dec.v_ref_usd is not None else 0.0
                            x_time_full = np.array([ts for ts in timebase])
                            cp_full = np.array(gains + P_t0, dtype=float)
                            pos_ts_set = set(pos_ts)
                            mask_pos = np.array([ts in pos_ts_set for ts in x_time_full])
                            x_time_use = x_time_full[mask_pos]
                            cp_vals_use = cp_full[mask_pos]

                            # Apply lookback window (decision gate's)
                            if dg.lookback_hours is not None and len(x_time_use) > 0:
                                t_cut = x_time_use[-1] - timedelta(hours=float(dg.lookback_hours))
                                mask_lkb = x_time_use >= t_cut
                                if mask_lkb.sum() >= max(dg.min_points, dg.polynomial_order + 1):
                                    x_time_use = x_time_use[mask_lkb]
                                    cp_vals_use = cp_vals_use[mask_lkb]

                            # Validate minimum points
                            if len(cp_vals_use) < max(dg.min_points, dg.polynomial_order + 1):
                                log.warning(
                                    f"Residual gate (fallback) skipped: asset={asset} reason=insufficient_points (n={len(cp_vals_use)})"
                                )
                                # Keep baseline decision based on total wmax across all wallets
                                total_wmax = sum(wb.wmax_usd for wb in dec.wmax_usd)
                                dec.decision = 1 if total_wmax > 0 else 0
                            else:
                                # Fit
                                t0_base_fb = x_time_use[0]
                                x_hours_fb = np.array([(ts - t0_base_fb).total_seconds() / 3600.0 for ts in x_time_use], dtype=float)
                                coeffs_fb = np.polyfit(x_hours_fb, cp_vals_use, dg.polynomial_order)
                                poly_fb = np.poly1d(coeffs_fb)
                                fitted_fb = poly_fb(x_hours_fb)
                                residuals_fb = cp_vals_use - fitted_fb

                                # Sigma (for display) and percentile thresholds
                                if dg.exclude_last_for_sigma and len(residuals_fb) > 1:
                                    res_est_fb = residuals_fb[:-1]
                                else:
                                    res_est_fb = residuals_fb
                                sigma_fb = float(np.std(res_est_fb, ddof=0))

                                if sigma_fb < dg.sigma_epsilon and dg.threshold_mode == "stddev":
                                    log.warning(
                                        f"Residual gate (fallback) skipped: asset={asset} reason=degenerate_sigma (sigma={sigma_fb:.6g})"
                                    )
                                    total_wmax = sum(wb.wmax_usd for wb in dec.wmax_usd)
                                    dec.decision = 1 if total_wmax > 0 else 0
                                else:
                                    r_now_fb = float(residuals_fb[-1])
                                    k_fb = float(dg.k_sigma)
                                    thr_low_fb = None
                                    thr_high_fb = None
                                    if dg.threshold_mode == "percentile":
                                        try:
                                            c_fb = float(dg.central_confidence)
                                            p_low_fb = max(0.0, (1.0 - c_fb) / 2.0)
                                            p_high_fb = min(1.0, 1.0 - p_low_fb)
                                            q_fb = np.quantile(res_est_fb, [p_low_fb, p_high_fb]) if len(res_est_fb) > 1 else np.array([0.0, 0.0])
                                            thr_low_fb = float(q_fb[0])
                                            thr_high_fb = float(q_fb[1])
                                            trig_fb = 1 if (r_now_fb > thr_high_fb) else 0
                                        except Exception:
                                            trig_fb = 1 if (sigma_fb > 0 and r_now_fb > k_fb * sigma_fb) else 0
                                    else:
                                        trig_fb = 1 if (sigma_fb > 0 and r_now_fb > k_fb * sigma_fb) else 0

                                    # Telemetry
                                    dec.residual_usd = r_now_fb
                                    dec.sigma_usd = sigma_fb
                                    dec.k_sigma = k_fb
                                    dec.residual_trigger = trig_fb
                                    dec.gate_applied = True

                                    log.info(
                                        f"Residual gate (fallback): asset={asset} residual_now={r_now_fb:.2f} sigma={sigma_fb:.2f} k={k_fb:.2f} triggered={trig_fb}"
                                    )

                                    # Decision from residual trigger
                                    dec.decision = trig_fb

                                    # Diagnostics (optional)
                                    dbg = settings.orchestrator.diagnostics
                                    if dbg.enabled:
                                        try:
                                            eff_lookback = dbg.lookback_hours_override if dbg.lookback_hours_override is not None else dg.lookback_hours
                                            # For diagnostics, reuse the filtered arrays used for fit
                                            residuals_fb_vec = (cp_vals_use - fitted_fb)
                                            out_path_dbg = plot_residual_composite(
                                                asset=asset,
                                                ref_mode=dec.ref_mode,
                                                timestamps=list(x_time_use),
                                                corrected_positions=cp_vals_use,
                                                fitted=fitted_fb,
                                                residuals=residuals_fb_vec,
                                                sigma=float(sigma_fb),
                                                k=float(k_fb),
                                                residual_now=float(r_now_fb),
                                                triggered=int(trig_fb),
                                                out_dir=str(dbg.dir),
                                                include_sigma_band=bool(dbg.include_sigma_band),
                                                include_k_sigma_band=bool(dbg.include_k_sigma_band),
                                                lookback_hours=eff_lookback,
                                                hist_samples_per_bin=int(getattr(dbg, "hist_samples_per_bin", 10)),
                                                threshold_mode=str(dg.threshold_mode),
                                                central_confidence=float(getattr(dg, "central_confidence", 0.68)),
                                                thr_low=thr_low_fb,
                                                thr_high=thr_high_fb,
                                            )
                                            dec.debug_plot_path = out_path_dbg
                                            log.info(f"Residual diagnostic (fallback) saved: asset={asset} path={out_path_dbg}")
                                        except Exception as pe:
                                            log.warning(f"Failed to save residual diagnostic (fallback) for asset={asset}: {pe}")
                        except Exception as ge:
                            log.warning(f"Residual gate (fallback) error for asset={asset}: {ge}. Using baseline decision.")
                            total_wmax = sum(wb.wmax_usd for wb in dec.wmax_usd)
                            dec.decision = 1 if total_wmax > 0 else 0
                    else:
                        # Baseline decision when gate disabled or not applied in fallback
                        total_wmax = sum(wb.wmax_usd for wb in dec.wmax_usd)
                        dec.decision = 1 if total_wmax > 0 else 0
                        # Visualization-only composite if diagnostics enabled
                        try:
                            dbg = settings.orchestrator.diagnostics
                            if dbg.enabled:
                                P_t0_v = float(dec.v_ref_usd) if dec.v_ref_usd is not None else 0.0
                                x_time_full_v = np.array([ts for ts in timebase])
                                cp_full_v = np.array(gains + P_t0_v, dtype=float)
                                pos_ts_set_v = set(pos_ts)
                                mask_pos_v = np.array([ts in pos_ts_set_v for ts in x_time_full_v])
                                x_time_v = x_time_full_v[mask_pos_v]
                                cp_vals_v = cp_full_v[mask_pos_v]
                                # Optional lookback (use gate's lookback for consistency)
                                if dg.lookback_hours is not None and len(x_time_v) > 0:
                                    t_cut_v = x_time_v[-1] - timedelta(hours=float(dg.lookback_hours))
                                    mask_lkb_v = x_time_v >= t_cut_v
                                    if mask_lkb_v.sum() >= max(dg.min_points, dg.polynomial_order + 1):
                                        x_time_v = x_time_v[mask_lkb_v]
                                        cp_vals_v = cp_vals_v[mask_lkb_v]
                                if len(cp_vals_v) >= max(dg.min_points, dg.polynomial_order + 1):
                                    t0_base_v = x_time_v[0]
                                    x_hours_v = np.array([(ts - t0_base_v).total_seconds() / 3600.0 for ts in x_time_v], dtype=float)
                                    # Model order: prefer normalized trend_indicator per-asset override; fallback to gate order
                                    try:
                                        from .config_normalizer import build_normalized_config
                                        _norm = build_normalized_config(settings)
                                        tiv = getattr(getattr(_norm, 'analysis', None), 'trend_indicator', None)
                                        poly_order_v = int(getattr(tiv, 'polynomial_order', dg.polynomial_order))
                                        pa = getattr(tiv, 'per_asset', {}) or {}
                                        ov = pa.get(str(asset).lower()) if isinstance(pa, dict) else None
                                        if isinstance(ov, dict) and ov.get('polynomial_order') is not None:
                                            poly_order_v = int(ov.get('polynomial_order'))
                                    except Exception:
                                        poly_order_v = dg.polynomial_order
                                    coeffs_v = np.polyfit(x_hours_v, cp_vals_v, poly_order_v)
                                    poly_v = np.poly1d(coeffs_v)
                                    fitted_v = poly_v(x_hours_v)
                                    residuals_v = cp_vals_v - fitted_v
                                else:
                                    # Flat-fit (mean) if insufficient points
                                    fitted_v = np.full_like(cp_vals_v, float(np.mean(cp_vals_v)) if len(cp_vals_v) else 0.0, dtype=float)
                                    residuals_v = cp_vals_v - fitted_v

                                # Thresholds
                                if dg.exclude_last_for_sigma and len(residuals_v) > 1:
                                    res_est_v = residuals_v[:-1]
                                else:
                                    res_est_v = residuals_v
                                sigma_v = float(np.std(res_est_v, ddof=0)) if len(res_est_v) > 0 else 0.0
                                thr_low_v = None
                                thr_high_v = None
                                if dg.threshold_mode == "percentile" and len(res_est_v) > 1:
                                    c_v = float(dg.central_confidence)
                                    p_low_v = max(0.0, (1.0 - c_v) / 2.0)
                                    p_high_v = min(1.0, 1.0 - p_low_v)
                                    q_v = np.quantile(res_est_v, [p_low_v, p_high_v])
                                    thr_low_v = float(q_v[0])
                                    thr_high_v = float(q_v[1])
                                r_now_v = float(residuals_v[-1]) if len(residuals_v) else 0.0
                                eff_lookback_v = dbg.lookback_hours_override if dbg.lookback_hours_override is not None else dg.lookback_hours
                                out_path_v = plot_residual_composite(
                                    asset=asset,
                                    ref_mode=dec.ref_mode,
                                    timestamps=list(x_time_v),
                                    corrected_positions=cp_vals_v,
                                    fitted=fitted_v,
                                    percent_base="first_valid_data",
                                    residuals=residuals_v,
                                    sigma=float(sigma_v),
                                    k=float(dg.k_sigma),
                                    residual_now=float(r_now_v),
                                    triggered=int(1 if (dg.threshold_mode == "percentile" and thr_high_v is not None and r_now_v > thr_high_v) else 0),
                                    out_dir=str(dbg.dir),
                                    include_sigma_band=bool(dbg.include_sigma_band),
                                    include_k_sigma_band=bool(dbg.include_k_sigma_band),
                                    lookback_hours=eff_lookback_v,
                                    hist_samples_per_bin=int(getattr(dbg, "hist_samples_per_bin", 10)),
                                    threshold_mode=str(dg.threshold_mode),
                                    central_confidence=float(getattr(dg, "central_confidence", 0.68)),
                                    thr_low=thr_low_v,
                                    thr_high=thr_high_v,
                                )
                                dec.debug_plot_path = out_path_v
                                log.info(f"Visualization diagnostic (fallback) saved: asset={asset} path={out_path_v}")
                        except Exception as ve:
                            log.warning(f"Visualization (fallback) failed: {ve}")
                    decisions[asset] = dec
                    continue
                else:
                    # Default/null fallback -> HOLD
                    series_obj0: Optional[AssetTimeSeries] = reader.fetch_asset_series(resolved, cfg.date_range)
                    if series_obj0 and series_obj0.series:
                        dec.v_t1_usd = float(series_obj0.series[max(series_obj0.series.keys())])
                    dec.decision = 0
                    dec.v_ref_usd = None
                    dec.g_usd = 0.0
                    dec.wmax_usd = []  # No wallet breakdown for null reference mode
                    dec.ref_mode = "null"
                    decisions[asset] = dec
                    continue
            t0, v0 = t0_v
            dec.v_ref_usd = float(v0)
            dec.ref_mode = "keyword"
            try:
                dec.t0_timestamp_seconds = float(t0.timestamp())
            except Exception:
                pass

            # Build per-asset date range starting at t0 (inclusive) to now
            per_range = DateRange(start=t0, end=now_utc)

            # Fetch series and transactions within [t0, now]
            series_obj: Optional[AssetTimeSeries] = reader.fetch_asset_series(resolved, per_range)
            if not series_obj or not series_obj.series:
                dec.decision = -1
                dec.error = "no series in range"
                decisions[asset] = dec
                continue

            # Latest V_i(t1)
            last_ts = max(series_obj.series.keys())
            v_t1 = float(series_obj.series[last_ts])
            dec.v_t1_usd = v_t1
            try:
                dec.t1_timestamp_seconds = float(last_ts.timestamp())
            except Exception:
                pass

            # Choose price_t1_usd from configured sources (annotation/telemetry only)
            chosen_price = None
            chosen_src = None
            try:
                from .config_normalizer import build_normalized_config
                _norm = build_normalized_config(settings)
                _logical = list(getattr(_norm.prices, 'logical_sources', []) or [])
            except Exception:
                _logical = []
            for src_name in (_logical or ["liqwid", "minswap"]):
                name = str(src_name).strip().lower()
                try:
                    if name == "greptime":
                        # Greptime has no direct price endpoint; use Liqwid first (same logical source), then Minswap
                        if chosen_price is None and ps_decision_liqwid is not None:
                            p = ps_decision_liqwid.get_latest_price_usd(asset)
                            if p is not None:
                                chosen_price = float(p); chosen_src = "greptime(liqwid)"; break
                        if chosen_price is None and ps_decision_minswap is not None:
                            p = ps_decision_minswap.get_latest_price_usd(asset)
                            if p is not None:
                                chosen_price = float(p); chosen_src = "greptime(minswap)"; break
                    elif name == "liqwid":
                        if ps_decision_liqwid is not None:
                            p = ps_decision_liqwid.get_latest_price_usd(asset)
                            if p is not None:
                                chosen_price = float(p); chosen_src = "liqwid"; break
                    elif name == "minswap":
                        if ps_decision_minswap is not None:
                            # Alias common names to wrapped canonical for Minswap
                            asset_min = canonicalize_minswap_asset(asset)
                            p = ps_decision_minswap.get_latest_price_usd(asset_min)
                            if p is not None:
                                chosen_price = float(p); chosen_src = "minswap"; break
                except Exception:
                    continue
            if chosen_price is not None:
                dec.price_t1_usd = float(chosen_price)
                try:
                    log.info(f"Decision price: asset={asset} source={chosen_src} price={chosen_price:.6g}")
                except Exception:
                    pass

            # Calculate per-wallet Wmax breakdown
            dec.wmax_usd = _calculate_per_wallet_wmax(
                reader=reader,
                cfg=cfg,
                resolved_symbol=resolved,
                date_range=per_range,
                safety_factor_c=settings.orchestrator.safety_factor.c,
                log=log
            )

            # Compute total gains for gate logic and telemetry
            # (Keep aggregated calculation for compatibility with residual gate)
            txs = reader.fetch_transactions(
                asset_symbol=resolved,
                deposits_prefix=cfg.deposits_prefix,
                withdrawals_prefix=cfg.withdrawals_prefix,
                date_range=per_range,
            )

            # Use mathematically correct gains with unified timebase & CDF(D/W)
            pos_ts = sorted(series_obj.series.keys())
            pos_vals = [float(series_obj.series[ts]) for ts in pos_ts]
            timebase, positions, deposit_cdf, withdrawal_cdf, gains = calculate_correct_gains(
                position_timestamps=pos_ts,
                position_values=pos_vals,
                transactions=txs,
                reference_time_index=0,
                interpolation_method="linear",
                alignment_method=str(cfg.alignment_method),
            )

            # G_i is last gains value since reference index is 0 due to start=t0
            g_i = float(gains[-1]) if len(gains) > 0 else 0.0
            dec.g_usd = g_i

            try:
                diag_cfg = getattr(settings.orchestrator, 'diagnostics', None)
                if (
                    str(asset).strip().lower() == "usdt"
                    and diag_cfg is not None
                    and bool(getattr(diag_cfg, 'enabled', False))
                    and getattr(diag_cfg, 'dir', None)
                ):
                    dump_asset_debug(
                        asset_display=asset,
                        resolved_symbol=resolved,
                        reader=reader,
                        cfg=cfg,
                        out_dir=str(getattr(diag_cfg, 'dir')),
                        alignment_method=str(cfg.alignment_method),
                        tx_timestamp_source=str(getattr(cfg, 'tx_timestamp_source', 'timestamp')),
                    )
            except Exception:
                pass
            
            # Baseline decision (when gate disabled or not applied)
            total_wmax = sum(wb.wmax_usd for wb in dec.wmax_usd)
            base_decision = 1 if total_wmax > 0 else 0

            # Residual gate for all assets (when enabled)
            dg = settings.orchestrator.decision_gate
            apply_gate = bool(dg.enabled)
            if apply_gate:
                # Build corrected position time series: P_corr(t) = P(t) - flows + baseline
                # Equivalent to gains + P(t0)
                try:
                    basis = str(getattr(dg, 'basis', 'corrected_position')).lower()
                    if basis == 'change_rate_usd':
                        # Use USD price series as the time series for gating
                        from . import io_adapters as ioa
                        from .io_adapters import DataSourceName as DataSourceNameT
                        from typing import cast
                        # Choose default greptime-backed provider from normalized prices
                        try:
                            from .config_normalizer import build_normalized_config as _bnc
                            _n = _bnc(settings)
                            prov = []
                            pr = getattr(_n, 'prices', None)
                            if pr is not None:
                                logicals = list(getattr(pr, 'logical_sources', []) or [])
                                prio = dict(getattr(pr, 'priority_by_logical', {}) or {})
                                seen = set()
                                keys = list(logicals) + [k for k in prio.keys() if k not in logicals]
                                for k in keys:
                                    for p in (prio.get(k, []) or []):
                                        ps = str(p).strip()
                                        if ps.lower().startswith('greptime(') and ps not in seen:
                                            prov.append(ps); seen.add(ps)
                            selected_source = prov[0] if prov else 'greptime(liqwid)'
                        except Exception:
                            selected_source = 'greptime(liqwid)'
                        try:
                            norm_duty = float(getattr(getattr(getattr(settings.orchestrator, 'prices_v2', None), 'duty_cycle_threshold', 0.9), '__float__', lambda: 0.9)())
                        except Exception:
                            norm_duty = 0.9
                        duty_thr = norm_duty
                        # Build series from configured source with duty-cycle fallback to greptime(liqwid)
                        rate_series = ioa.get_price_series(resolved, cast(DataSourceNameT, selected_source), settings.client, per_range)
                        dc = ioa.compute_duty_cycle(rate_series, per_range)
                        if dc < duty_thr and selected_source != 'greptime(liqwid)':
                            fb = 'greptime(liqwid)'
                            rate_fb = ioa.get_price_series(resolved, cast(DataSourceNameT, fb), settings.client, per_range)
                            if rate_fb and rate_fb.series:
                                rate_series = rate_fb
                        if not rate_series or not rate_series.series:
                            log.warning(f"Gate(basis=change_rate_usd) skipped for {asset}: no rate series")
                            dec.decision = 0
                            dec.gate_applied = False
                            decisions[asset] = dec
                            continue
                        x_time = np.array(sorted(rate_series.series.keys()))
                        cp_vals = np.array([float(rate_series.series[t]) for t in x_time], dtype=float)
                        # Optional lookback window
                        if dg.lookback_hours is not None and len(x_time) > 0:
                            t_cut = x_time[-1] - timedelta(hours=float(dg.lookback_hours))
                            mask_lkb = x_time >= t_cut
                            if mask_lkb.sum() >= max(dg.min_points, dg.polynomial_order + 1):
                                x_time = x_time[mask_lkb]
                                cp_vals = cp_vals[mask_lkb]
                    else:
                        # Default basis: corrected positions (gains + P_t0)
                        P_t0 = float(dec.v_ref_usd) if dec.v_ref_usd is not None else float(0.0)
                        corrected_positions = gains + P_t0  # numpy array
                        # Start with full unified timebase arrays
                        x_time = np.array([ts for ts in timebase])
                        cp_vals = np.array(corrected_positions, dtype=float)
                        # Apply optional lookback window boundary
                        if dg.lookback_hours is not None:
                            t_cut = x_time[-1] - timedelta(hours=float(dg.lookback_hours))
                        else:
                            t_cut = None
                        # Restrict to original position timestamps only (align with client plots)
                        pos_ts_set = set(pos_ts)
                        if t_cut is not None:
                            pos_ts_set = {ts for ts in pos_ts_set if ts >= t_cut}
                        mask_pos = np.array([ts in pos_ts_set for ts in x_time])
                        x_time = x_time[mask_pos]
                        cp_vals = cp_vals[mask_pos]

                    # Ensure we still have enough samples after filtering
                    if len(cp_vals) < max(dg.min_points, dg.polynomial_order + 1):
                        log.warning(
                            f"Residual gate skipped: asset={asset} reason=insufficient_points "
                            f"(n={len(cp_vals)}, min_points={dg.min_points}, order={dg.polynomial_order})"
                        )
                        dec.decision = 0
                        dec.gate_applied = False
                        # Generate diagnostic anyway with flat fit
                        try:
                            dbg = settings.orchestrator.diagnostics
                            if dbg.enabled:
                                fitted_dbg = np.full_like(cp_vals, float(np.mean(cp_vals)) if len(cp_vals) else 0.0, dtype=float)
                                residuals_dbg = (cp_vals - fitted_dbg)
                                out_path = plot_residual_composite(
                                    asset=asset,
                                    ref_mode=dec.ref_mode,
                                    timestamps=list(x_time),
                                    corrected_positions=cp_vals,
                                    fitted=fitted_dbg,
                                    residuals=residuals_dbg,
                                    sigma=0.0,
                                    k=float(dg.k_sigma),
                                    residual_now=float(residuals_dbg[-1]) if len(residuals_dbg) else 0.0,
                                    triggered=0,
                                    out_dir=str(dbg.dir),
                                    include_sigma_band=bool(dbg.include_sigma_band),
                                    include_k_sigma_band=bool(dbg.include_k_sigma_band),
                                    lookback_hours=dg.lookback_hours,
                                    hist_samples_per_bin=int(getattr(dbg, "hist_samples_per_bin", 10)),
                                )
                                dec.debug_plot_path = out_path
                                log.info(f"Residual diagnostic (insufficient points) saved: asset={asset} path={out_path}")
                        except Exception as pe:
                            log.warning(f"Failed to save residual diagnostic (insufficient points) for asset={asset}: {pe}")
                        decisions[asset] = dec
                        continue

                    # Fit baseline according to method
                    if str(getattr(dg, 'method', 'polynomial_fit')).lower() == 'median':
                        med = float(np.median(cp_vals)) if len(cp_vals) else 0.0
                        fitted = np.full_like(cp_vals, med, dtype=float)
                    else:
                        # Convert times to hours since start
                        t0_base = x_time[0]
                        x_hours = np.array([(ts - t0_base).total_seconds() / 3600.0 for ts in x_time], dtype=float)
                        coeffs = np.polyfit(x_hours, cp_vals, dg.polynomial_order)
                        poly = np.poly1d(coeffs)
                        fitted = poly(x_hours)
                    residuals = cp_vals - fitted

                    # Sigma (for display) and optional percentile thresholds
                    if dg.exclude_last_for_sigma and len(residuals) > 1:
                        res_est = residuals[:-1]
                    else:
                        res_est = residuals
                    sigma = float(np.std(res_est, ddof=0))

                    # Degenerate distribution check
                    if sigma < dg.sigma_epsilon and dg.threshold_mode == "stddev":
                        log.warning(
                            f"Residual gate skipped: asset={asset} reason=degenerate_sigma (sigma={sigma:.6g}, eps={dg.sigma_epsilon})"
                        )
                        dec.decision = 0
                        dec.gate_applied = False
                        # Save diagnostic with computed fit and sigma ~ 0
                        try:
                            dbg = settings.orchestrator.diagnostics
                            if dbg.enabled:
                                out_path = plot_residual_composite(
                                    asset=asset,
                                    ref_mode=dec.ref_mode,
                                    timestamps=list(x_time),
                                    corrected_positions=cp_vals,
                                    fitted=fitted,
                                    percent_base="first_valid_data",
                                    residuals=residuals,
                                    sigma=float(sigma),
                                    k=float(dg.k_sigma),
                                    residual_now=float(residuals[-1]) if len(residuals) else 0.0,
                                    triggered=0,
                                    out_dir=str(dbg.dir),
                                    include_sigma_band=bool(dbg.include_sigma_band),
                                    include_k_sigma_band=bool(dbg.include_k_sigma_band),
                                    lookback_hours=dg.lookback_hours,
                                    hist_samples_per_bin=int(getattr(dbg, "hist_samples_per_bin", 10)),
                                )
                                dec.debug_plot_path = out_path
                                log.info(f"Residual diagnostic (degenerate sigma) saved: asset={asset} path={out_path}")
                        except Exception as pe:
                            log.warning(f"Failed to save residual diagnostic (degenerate sigma) for asset={asset}: {pe}")
                        decisions[asset] = dec
                        continue
                    # Evaluate residual at the last observed position sample
                    r_now = float(residuals[-1])
                    k = float(dg.k_sigma)
                    # Thresholding: stddev (k*sigma) or percentile central interval
                    thr_low = None
                    thr_high = None
                    if dg.threshold_mode == "percentile":
                        try:
                            c = float(dg.central_confidence)
                            p_low = max(0.0, (1.0 - c) / 2.0)
                            p_high = min(1.0, 1.0 - p_low)
                            q = np.quantile(res_est, [p_low, p_high]) if len(res_est) > 1 else np.array([0.0, 0.0])
                            thr_low = float(q[0])
                            thr_high = float(q[1])
                            triggered = 1 if (r_now > thr_high) else 0
                        except Exception as _:
                            # Fallback to stddev if percentiles fail
                            triggered = 1 if (sigma > 0 and r_now > k * sigma) else 0
                    else:
                        triggered = 1 if (sigma > 0 and r_now > k * sigma) else 0
                    # Populate telemetry/context
                    dec.residual_usd = r_now
                    dec.sigma_usd = sigma
                    dec.k_sigma = k
                    dec.residual_trigger = triggered
                    dec.gate_applied = True

                    log.info(
                        f"Residual gate: asset={asset} residual_now={r_now:.2f} sigma={sigma:.2f} k={k:.2f} triggered={triggered}"
                    )

                    # Final decision uses residual trigger only
                    dec.decision = triggered

                    # Optional diagnostics plot
                    dbg = settings.orchestrator.diagnostics
                    if dbg.enabled:
                        try:
                            eff_lookback = dbg.lookback_hours_override if dbg.lookback_hours_override is not None else dg.lookback_hours
                            residuals_vec = (cp_vals - fitted)
                            out_path = plot_residual_composite(
                                asset=asset,
                                ref_mode=dec.ref_mode,
                                timestamps=list(x_time),
                                corrected_positions=cp_vals,
                                fitted=fitted,
                                residuals=residuals_vec,
                                sigma=float(sigma),
                                k=float(k),
                                residual_now=float(r_now),
                                triggered=int(triggered),
                                out_dir=str(dbg.dir),
                                include_sigma_band=bool(dbg.include_sigma_band),
                                include_k_sigma_band=bool(dbg.include_k_sigma_band),
                                lookback_hours=eff_lookback,
                                hist_samples_per_bin=int(getattr(dbg, "hist_samples_per_bin", 10)),
                                threshold_mode=str(dg.threshold_mode),
                                central_confidence=float(getattr(dg, "central_confidence", 0.68)),
                                thr_low=thr_low,
                                thr_high=thr_high,
                            )
                            dec.debug_plot_path = out_path
                            log.info(f"Residual diagnostic saved: asset={asset} path={out_path}")
                        except Exception as pe:
                            log.warning(f"Failed to save residual diagnostic for asset={asset}: {pe}")
                except Exception as ge:
                    log.warning(f"Residual gate error for asset={asset}: {ge}. Treating as not triggered.")
                    dec.decision = 0
                    dec.gate_applied = False
            else:
                # Gate disabled -> baseline decision; optional diagnostics
                dec.decision = base_decision
                try:
                    dbg = settings.orchestrator.diagnostics
                    if dbg.enabled:
                        # Build corrected positions same as above
                        P_t0 = float(dec.v_ref_usd) if dec.v_ref_usd is not None else float(0.0)
                        corrected_positions = gains + P_t0
                        x_time = np.array([ts for ts in timebase])
                        cp_vals = np.array(corrected_positions, dtype=float)
                        # Restrict to original position timestamps
                        pos_ts_set = set(pos_ts)
                        mask_pos = np.array([ts in pos_ts_set for ts in x_time])
                        x_time = x_time[mask_pos]
                        cp_vals = cp_vals[mask_pos]
                        # Optionally apply lookback for visualization
                        if dg.lookback_hours is not None and len(x_time) > 0:
                            t_cut = x_time[-1] - timedelta(hours=float(dg.lookback_hours))
                            mask_lkb = x_time >= t_cut
                            if mask_lkb.sum() >= max(dg.min_points, dg.polynomial_order + 1):
                                x_time = x_time[mask_lkb]
                                cp_vals = cp_vals[mask_lkb]
                        # Fit if enough points
                        if len(cp_vals) >= max(dg.min_points, dg.polynomial_order + 1):
                            t0_base_v = x_time[0]
                            x_hours_v = np.array([(ts - t0_base_v).total_seconds() / 3600.0 for ts in x_time], dtype=float)
                            # Choose model: prefer normalized trend_indicator per-asset override; fallback to gate order
                            try:
                                from .config_normalizer import build_normalized_config
                                _norm = build_normalized_config(settings)
                                tiv = getattr(getattr(_norm, 'analysis', None), 'trend_indicator', None)
                                poly_order_v = int(getattr(tiv, 'polynomial_order', dg.polynomial_order))
                                pa = getattr(tiv, 'per_asset', {}) or {}
                                ov = pa.get(str(asset).lower()) if isinstance(pa, dict) else None
                                if isinstance(ov, dict) and ov.get('polynomial_order') is not None:
                                    poly_order_v = int(ov.get('polynomial_order'))
                            except Exception:
                                poly_order_v = dg.polynomial_order
                            coeffs_v = np.polyfit(x_hours_v, cp_vals, poly_order_v)
                            poly_v = np.poly1d(coeffs_v)
                            fitted_v = poly_v(x_hours_v)
                            residuals_v = cp_vals - fitted_v
                            # Thresholds for visualization
                            if dg.exclude_last_for_sigma and len(residuals_v) > 1:
                                res_est_v = residuals_v[:-1]
                            else:
                                res_est_v = residuals_v
                            sigma_v = float(np.std(res_est_v, ddof=0))
                            thr_low_v = None
                            thr_high_v = None
                            if dg.threshold_mode == "percentile" and len(res_est_v) > 1:
                                c_v = float(dg.central_confidence)
                                p_low_v = max(0.0, (1.0 - c_v) / 2.0)
                                p_high_v = min(1.0, 1.0 - p_low_v)
                                q_v = np.quantile(res_est_v, [p_low_v, p_high_v])
                                thr_low_v = float(q_v[0])
                                thr_high_v = float(q_v[1])
                            r_now_v = float(residuals_v[-1]) if len(residuals_v) else 0.0
                            out_path_v = plot_residual_composite(
                                asset=asset,
                                ref_mode=dec.ref_mode,
                                timestamps=list(x_time),
                                corrected_positions=cp_vals,
                                fitted=fitted_v,
                                residuals=residuals_v,
                                sigma=float(sigma_v),
                                k=float(dg.k_sigma),
                                residual_now=float(r_now_v),
                                triggered=int(1 if (dg.threshold_mode == "percentile" and thr_high_v is not None and r_now_v > thr_high_v) else 0),
                                out_dir=str(dbg.dir),
                                include_sigma_band=bool(dbg.include_sigma_band),
                                include_k_sigma_band=bool(dbg.include_k_sigma_band),
                                lookback_hours=dg.lookback_hours,
                                hist_samples_per_bin=int(getattr(dbg, "hist_samples_per_bin", 10)),
                                threshold_mode=str(dg.threshold_mode),
                                central_confidence=float(getattr(dg, "central_confidence", 0.68)),
                                thr_low=thr_low_v,
                                thr_high=thr_high_v,
                            )
                            dec.debug_plot_path = out_path_v
                        else:
                            # Not enough points -> flat fit visualization (mean)
                            fitted_v = np.full_like(cp_vals, float(np.mean(cp_vals)) if len(cp_vals) else 0.0, dtype=float)
                            residuals_v = cp_vals - fitted_v
                            if dg.exclude_last_for_sigma and len(residuals_v) > 1:
                                res_est_v = residuals_v[:-1]
                            else:
                                res_est_v = residuals_v
                            sigma_v = float(np.std(res_est_v, ddof=0)) if len(res_est_v) > 0 else 0.0
                            thr_low_v = None
                            thr_high_v = None
                            if dg.threshold_mode == "percentile" and len(res_est_v) > 1:
                                c_v = float(dg.central_confidence)
                                p_low_v = max(0.0, (1.0 - c_v) / 2.0)
                                p_high_v = min(1.0, 1.0 - p_low_v)
                                q_v = np.quantile(res_est_v, [p_low_v, p_high_v])
                                thr_low_v = float(q_v[0])
                                thr_high_v = float(q_v[1])
                            r_now_v = float(residuals_v[-1]) if len(residuals_v) else 0.0
                            out_path_v = plot_residual_composite(
                                asset=asset,
                                ref_mode=dec.ref_mode,
                                timestamps=list(x_time),
                                corrected_positions=cp_vals,
                                fitted=fitted_v,
                                residuals=residuals_v,
                                sigma=float(sigma_v),
                                k=float(dg.k_sigma),
                                residual_now=float(r_now_v),
                                triggered=int(1 if (dg.threshold_mode == "percentile" and thr_high_v is not None and r_now_v > thr_high_v) else 0),
                                out_dir=str(dbg.dir),
                                include_sigma_band=bool(dbg.include_sigma_band),
                                include_k_sigma_band=bool(dbg.include_k_sigma_band),
                                lookback_hours=dg.lookback_hours,
                                hist_samples_per_bin=int(getattr(dbg, "hist_samples_per_bin", 10)),
                                threshold_mode=str(dg.threshold_mode),
                                central_confidence=float(getattr(dg, "central_confidence", 0.68)),
                                thr_low=thr_low_v,
                                thr_high=thr_high_v,
                            )
                            dec.debug_plot_path = out_path_v
                except Exception as ve:
                    log.warning(f"Visualization failed: {ve}")
            decisions[asset] = dec
        except Exception as e:
            dec.decision = -1
            dec.error = str(e)
            decisions[asset] = dec

    # ===== Phase C: Price-source comparison (post-process decisions) =====
    # v2-only: run comparison only when analysis.price_compare is enabled
    pc2 = None
    try:
        pc2 = getattr(getattr(settings.orchestrator, 'analysis_v2', None), 'price_compare', None)
    except Exception:
        pc2 = None
    use_pc2 = bool(pc2 and getattr(pc2, 'enabled', False))
    if use_pc2:
        # Load registry from config directory
        try:
            reg_path = settings.config_path.parent / "token_registry.csv"
            registry = load_registry(str(reg_path))
        except Exception as e:
            log.error(f"Price compare: failed to load token registry: {e}")
            registry = None

        # Log selected sources and endpoints
        apis = getattr(settings.orchestrator, "apis", None)
        # Determine logical sources to compare
        logical_sources: list[str] = []
        if use_pc2:
            try:
                logical_sources = [str(s).strip().lower() for s in (pc2.sources or []) if str(s).strip()]
            except Exception:
                logical_sources = []
        if not logical_sources:
            # Fallback to normalized prices logicals or default
            try:
                from .config_normalizer import build_normalized_config
                _norm = build_normalized_config(settings)
                logical_sources = list(getattr(_norm.prices, 'logical_sources', []) or [])
            except Exception:
                logical_sources = []
        if not logical_sources:
            logical_sources = ["liqwid", "minswap"]
        # De-duplicate while preserving order
        seen_ls = set()
        logical_sources = [s for s in logical_sources if not (s in seen_ls or seen_ls.add(s))]

        log.info(
            "Price compare config: sources=%s apis(minswap=%s liqwid=%s)",
            ",".join(logical_sources),
            getattr(apis, "minswap_aggregator", None) if apis else None,
            getattr(apis, "liqwid_graphql", None) if apis else None,
        )

        # Build sources as configured (use centralized orchestrator.apis)
        # Effective PC settings (defaults and overrides) from v2
        currency = str(getattr(pc2, 'currency', 'usd'))
        req_timeout = int(getattr(pc2, 'request_timeout_seconds', 5))
        retries = int(getattr(pc2, 'retries', 1))
        persistence_threshold = int(getattr(pc2, 'persistence_threshold', 1))
        action_on_mismatch = str(getattr(pc2, 'action_on_mismatch', 'hold')).strip().lower()
        per_asset_overrides = dict(getattr(pc2, 'per_asset_overrides', {}) or {})
        eps_mode_default = str(getattr(pc2, 'epsilon_mode', 'relative')).strip().lower()
        eps_default = float(getattr(pc2, 'tolerance_epsilon', 0.01))

        def _make_source(name: str):
            name = (name or "").strip().lower()
            apis = getattr(settings.orchestrator, "apis", None)
            if name == "minswap" and registry is not None and apis and getattr(apis, "minswap_aggregator", None):
                return MinswapAggregatorPriceSource(
                    base_url=str(apis.minswap_aggregator),
                    currency=currency,
                    timeout_s=req_timeout,
                    retries=retries,
                    registry=registry,
                )
            if name == "liqwid" and apis and getattr(apis, "liqwid_graphql", None):
                return LiqwidGraphQLPriceSource(endpoint=str(apis.liqwid_graphql), timeout_s=10)
            return None

        # Build resolver against liqwid supply for symbol resolution when needed
        try:
            resolver = Resolver(greptime_reader=reader)
        except Exception:
            resolver = None

        # Source priority per logical source (prefer normalized prices.priority_by_logical)
        try:
            from .config_normalizer import build_normalized_config
            _norm = build_normalized_config(settings)
            psp = dict(getattr(_norm.prices, 'priority_by_logical', {}) or {})
        except Exception:
            psp = getattr(getattr(settings.orchestrator, 'telemetry', None), 'price_source_priority', {}) or {}

        def _price_from_greptime(pref: str, logical: str, asset_name: str) -> Optional[float]:
            s = (pref or "").strip().lower()
            if not (s.startswith("greptime(") and s.endswith(")")):
                return None
            dbname = s[len("greptime("):-1]
            try:
                import copy
                gcfg = copy.deepcopy(settings.client.greptime)
                gcfg.database = dbname
                # Choose proper prefix
                if dbname == "liqwid":
                    tprefix = settings.client.table_asset_prefix
                    # Resolve to liqwid underlying/supply symbol if resolver is available
                    sym = asset_name
                    if resolver is not None:
                        try:
                            _mid, sym_res = resolver.resolve_asset(asset_name)
                            sym = sym_res or asset_name
                        except Exception:
                            sym = asset_name
                elif dbname == "minswap":
                    tprefix = "minswap_prices_"
                    # Route aliases to canonical wrapped tables for Minswap
                    sym = canonicalize_minswap_asset(asset_name)
                else:
                    return None
                from ..shared.greptime_reader import GreptimeReader as _GR
                # Cache GreptimeReader instances per (db, prefix) to avoid noisy re-initialization
                global _GR_CACHE
                try:
                    _GR_CACHE
                except NameError:
                    _GR_CACHE = {}
                key = (str(dbname), str(tprefix))
                gr = _GR_CACHE.get(key)
                if gr is None:
                    gr = _GR(gcfg, tprefix)
                    _GR_CACHE[key] = gr
                return gr.fetch_latest_price_usd(sym)
            except Exception:
                return None

        def _price_from_api(pref: str, logical: str, asset_name: str) -> Optional[float]:
            s = (pref or "").strip().lower()
            # Direct API sources
            if s == "liqwid":
                src = _make_source("liqwid")
                return src.get_latest_price_usd(asset_name) if src else None
            if s == "minswap":
                src = _make_source("minswap")
                return src.get_latest_price_usd(asset_name) if src else None
            return None

        def _resolve_logical_price(logical: str, asset_name: str) -> Optional[float]:
            prefs = psp.get(logical, [])
            for pref in prefs:
                # try greptime first if specified in list order
                v = _price_from_greptime(pref, logical, asset_name)
                if v is not None:
                    return float(v)
                v = _price_from_api(pref, logical, asset_name)
                if v is not None:
                    return float(v)
            return None

        # Per-asset overrides helper
        def _get_eps(asset_name: str) -> tuple[str, float]:
            ov = None
            try:
                # allow both exact and lowercase keys
                ov = per_asset_overrides.get(asset_name)
                if ov is None:
                    ov = per_asset_overrides.get(asset_name.lower())
            except Exception:
                ov = None
            mode = str((getattr(ov, 'epsilon_mode', None) if not isinstance(ov, dict) else ov.get('epsilon_mode')) or eps_mode_default)
            eps = float((getattr(ov, 'tolerance_epsilon', None) if not isinstance(ov, dict) else ov.get('tolerance_epsilon')) or eps_default)
            return (mode, eps)

        # Simple in-memory persistence of mismatch counts
        global _PRICE_MISMATCH_COUNTS
        try:
            _PRICE_MISMATCH_COUNTS
        except NameError:
            _PRICE_MISMATCH_COUNTS = {}

        for asset, dec in decisions.items():
            try:
                if dec.decision == -1:
                    continue  # keep error state
                # Fetch prices per logical source using priority list
                prices: Dict[str, float] = {}
                for logical in logical_sources:
                    try:
                        p = _resolve_logical_price(logical, asset)
                        if p is not None:
                            prices[logical] = float(p)
                    except Exception:
                        continue
                dec.prices_by_source = prices
                if len(prices) < 2:
                    log.info(
                        "Price compare unavailable: asset=%s sources=%s",
                        asset,
                        ",".join(sorted(prices.keys())) or "None",
                    )
                    dec.price_compare_unavailable = 1
                    dec.price_mismatch = 0
                    dec.price_delta_abs = None
                    dec.price_delta_rel = None
                    continue
                dec.price_compare_unavailable = 0
                max_p = max(prices.values())
                min_p = min(prices.values())
                d_abs = max_p - min_p
                d_rel = (d_abs / max_p) if max_p > 0 else 0.0
                dec.price_delta_abs = d_abs
                dec.price_delta_rel = d_rel
                mode, eps = _get_eps(asset)
                if str(mode).lower() == "absolute":
                    mismatch = 1 if d_abs > float(eps) else 0
                else:
                    mismatch = 1 if d_rel > float(eps) else 0
                prev = _PRICE_MISMATCH_COUNTS.get(asset, 0)
                curr = (prev + 1) if mismatch else 0
                _PRICE_MISMATCH_COUNTS[asset] = curr
                dec.price_mismatch = mismatch
                if mismatch and curr >= int(persistence_threshold) and str(action_on_mismatch) == "hold":
                    dec.decision = 0
                    try:
                        # Log the extreme sources and their values instead of undefined p1/p2
                        max_src = max(prices, key=prices.get)
                        min_src = min(prices, key=prices.get)
                        log.warning(
                            "Price compare HOLD: asset=%s %s=%.6g %s=%.6g d_abs=%.6g d_rel=%.6g mode=%s eps=%s",
                            asset,
                            min_src, prices[min_src],
                            max_src, prices[max_src],
                            d_abs, d_rel,
                            mode, eps,
                        )
                    except Exception:
                        # Fallback logging
                        log.warning(
                            "Price compare HOLD: asset=%s d_abs=%.6g d_rel=%.6g mode=%s eps=%s",
                            asset, d_abs, d_rel, mode, eps
                        )
            except Exception as pe:
                log.warning(f"Price compare failed for asset={asset}: {pe}")

    return decisions

