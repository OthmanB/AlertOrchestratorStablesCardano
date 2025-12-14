#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Diagnostics plotting utilities for Alert Orchestrator

Produces PNG plots that visualize the residual gating decision exactly as
computed by alert_logic.evaluate_once():
- Corrected positions since t0
- Polynomial fit used for gating
- Optional ±sigma band and ±k*sigma band
- Annotated last point and trigger state
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence
from datetime import datetime

import matplotlib
matplotlib.use("Agg")  # non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np


def _gaussian_kde(grid: np.ndarray, data: np.ndarray) -> Optional[np.ndarray]:
    """Simple Gaussian KDE without external deps.
    Returns density evaluated on grid, or None if not computable.
    """
    if data.size < 2:
        return None
    std = float(np.std(data))
    if not np.isfinite(std) or std <= 0:
        return None
    n = data.size
    # Silverman's rule of thumb
    h = 1.06 * std * (n ** (-1.0 / 5.0))
    if not np.isfinite(h) or h <= 0:
        return None
    # Broadcast compute
    z = (grid[:, None] - data[None, :]) / h
    norm = (1.0 / np.sqrt(2.0 * np.pi)) * np.exp(-0.5 * z * z)
    dens = norm.mean(axis=1) / h
    return dens


def plot_residual_composite(
    *,
    asset: str,
    ref_mode: Optional[str],
    timestamps: Sequence[datetime],
    corrected_positions: np.ndarray,
    fitted: np.ndarray,
    residuals: np.ndarray,
    sigma: float,
    k: float,
    residual_now: float,
    triggered: int,
    out_dir: str,
    include_sigma_band: bool = True,
    include_k_sigma_band: bool = True,
    lookback_hours: Optional[float] = None,
    hist_samples_per_bin: int = 10,
    threshold_mode: str = "stddev",
    central_confidence: Optional[float] = None,
    thr_low: Optional[float] = None,
    thr_high: Optional[float] = None,
    # New: control y-axis units (absolute USD, percent of a base, or raw rate values)
    yaxis_mode: str = "absolute",  # "absolute" | "percent" | "rate"
    percent_base: str = "first_valid_data",  # "fit_median" | "fit_last" | "fit_first" | "first_valid_data"
    # New: transaction event markers
    deposit_timestamps: Optional[Sequence[datetime]] = None,
    withdrawal_timestamps: Optional[Sequence[datetime]] = None,
    # New: optional overlays for trend (always informative) and decision (basis-specific)
    trend_center: Optional[np.ndarray] = None,
    trend_band_lo: Optional[np.ndarray] = None,
    trend_band_hi: Optional[np.ndarray] = None,
    decision_center: Optional[np.ndarray] = None,
    decision_band_lo: Optional[np.ndarray] = None,
    decision_band_hi: Optional[np.ndarray] = None,
    # Phase E: Aggregation support (whisker plots)
    aggregated_data: Optional[dict] = None,  # {bin_centers: ndarray, p10: ndarray, p25: ndarray, p50: ndarray, p75: ndarray, p90: ndarray, count: ndarray}
    show_raw_points: bool = True,  # Whether to show individual points when aggregation is active
) -> str:
    """
    Save a composite diagnostic plot (3 panels) for the residual gating:
    - Top-left: Data + Model with bands
    - Bottom-left: Residuals over time with 0/±σ/±kσ
    - Right: Residual distribution (histogram + optional KDE)
    """
    fontsize=6
    markersize = 3
    # Ensure arrays
    ts = np.array(list(timestamps))
    y = np.array(corrected_positions, dtype=float)
    y_fit = np.array(fitted, dtype=float)
    r = np.array(residuals, dtype=float)

    # Determine normalization for percent mode (single scalar for consistency)
    def _finite(val: float) -> bool:
        return np.isfinite(val) and val is not None

    mode = str(yaxis_mode).lower()
    use_percent = mode == "percent"
    use_rate = mode == "rate"  # Rate views: no normalization, raw values
    base = str(percent_base).lower()
    
    # For rate views, skip normalization entirely (denom = 1.0, scale = 1.0)
    if use_rate:
        denom = 1.0
    elif base == "fit_last" and y_fit.size > 0 and _finite(float(y_fit[-1])):
        denom = float(y_fit[-1])
    elif base == "fit_median" and y_fit.size > 0 and _finite(float(np.nanmedian(y_fit))):
        ## default to robust central tendency of fit
        med = float(np.nanmedian(y_fit)) if y_fit.size > 0 else np.nan
        if _finite(med) and abs(med) > 0:
            denom = med
        else:
            # fallback: mean of abs(y_fit) or 1.0
            mean_abs = float(np.nanmean(np.abs(y_fit))) if y_fit.size > 0 else np.nan
            denom = mean_abs if _finite(mean_abs) and abs(mean_abs) > 0 else 1.0
     # use denom as the y_fit(min(timestamps))
    elif base == "fit_first" and y_fit.size > 0:
        # first valid (finite, non-nan and non-zero) value for denom
        valid_pos = np.where(np.isfinite(y_fit) & (np.abs(y_fit) > 0))[0]
        denom = float(y_fit[valid_pos[0]])
    # fit valid data point as denominator
    elif base == "first_valid_data" and y.size > 0:
        valid_pos = np.where(np.isfinite(y) & (np.abs(y) > 0))[0]
        if valid_pos.size > 0:
            denom = float(y[valid_pos[0]])
        else:
            denom = 1.0
    else:
        denom = 1.0
    print("denom:", denom, " base:", base)
    scale = 100.0 if use_percent else 1.0
    # Pre-scale series for plotting
    y_plot = (y / denom) * scale if use_percent else y
    y_fit_plot = (y_fit / denom) * scale if use_percent else y_fit
    r_plot = (r / denom) * scale if use_percent else r
    sigma_plot = (float(sigma) / denom) * scale if (use_percent and np.isfinite(sigma)) else float(sigma)
    thr_low_plot = (float(thr_low) / denom) * scale if (use_percent and thr_low is not None) else thr_low
    thr_high_plot = (float(thr_high) / denom) * scale if (use_percent and thr_high is not None) else thr_high
    residual_now_plot = (float(residual_now) / denom) * scale if (use_percent and np.isfinite(residual_now)) else float(residual_now)

    # Normalize overlays if provided
    def _norm_arr(arr: Optional[np.ndarray]) -> Optional[np.ndarray]:
        if arr is None:
            return None
        try:
            arr = np.asarray(arr, dtype=float)
            return (arr / denom) * scale if use_percent else arr
        except Exception:
            return None
    trend_center_plot = _norm_arr(trend_center)
    trend_band_lo_plot = _norm_arr(trend_band_lo)
    trend_band_hi_plot = _norm_arr(trend_band_hi)
    decision_center_plot = _norm_arr(decision_center)
    decision_band_lo_plot = _norm_arr(decision_band_lo)
    decision_band_hi_plot = _norm_arr(decision_band_hi)

    # compute gains as per the model over the fit range
    model_gains = y_fit_plot[-1] - y_fit_plot[0]
    interval_model_gains = ts[-1] - ts[0]  # timedelta object
    interval_days = interval_model_gains.total_seconds() / 86400.0  # convert to float days
    model_gains_per_day = model_gains / interval_days
    model_gains_per_year = model_gains_per_day * 365
    model_gains_per_month = model_gains_per_day * 30


    # Figure layout
    fig = plt.figure(figsize=(14, 7))
    gs = fig.add_gridspec(2, 2, width_ratios=[2.0, 1.0], height_ratios=[1.0, 1.0], wspace=0.25, hspace=0.25)
    ax_main = fig.add_subplot(gs[0, 0])
    ax_res = fig.add_subplot(gs[1, 0], sharex=ax_main)
    ax_pdf = fig.add_subplot(gs[:, 1])

    # Main: corrected position + fit
    # Phase E: If aggregated data provided, render proper whisker plots (candlestick-style)
    if aggregated_data is not None and 'bin_centers' in aggregated_data:
        try:
            bin_centers = np.asarray(aggregated_data['bin_centers'])
            stats = aggregated_data  # Dict with p10, p25, p50, p75, p90 arrays
            
            # Normalize aggregated stats for percent mode
            if use_percent:
                stats_norm = {k: (v / denom) * scale if k.startswith('p') else v 
                              for k, v in stats.items()}
            else:
                stats_norm = stats
            
            # Extract percentiles
            p10 = np.asarray(stats_norm.get('p10', []))
            p25 = np.asarray(stats_norm.get('p25', []))
            p50 = np.asarray(stats_norm.get('p50', []))
            p75 = np.asarray(stats_norm.get('p75', []))
            p90 = np.asarray(stats_norm.get('p90', []))
            
            # Determine color for each bin (green if rising, red if falling)
            # Use first and last values in the bin (approximated by p25 and p75 as proxies)
            colors = []
            for i in range(len(bin_centers)):
                # Green if p75 >= p25 (rising), red otherwise
                color = '#27AE60' if p75[i] >= p25[i] else '#E74C3C'
                colors.append(color)
            
            # Draw whisker plots for each bin
            width_hours = 0.3  # Width of the candlestick box in hours
            if len(bin_centers) > 1:
                # Estimate time interval between bins
                dt_seconds = (bin_centers[1] - bin_centers[0]).total_seconds()
                width_hours = dt_seconds / 3600.0 * 0.6  # 60% of interval
            width = width_hours / 24.0  # Convert to days for matplotlib
            
            for i in range(len(bin_centers)):
                center = bin_centers[i]
                color = colors[i]
                
                # Main rectangle (p25 to p75) - filled box
                rect_bottom = min(p25[i], p75[i])
                rect_height = abs(p75[i] - p25[i])
                ax_main.add_patch(plt.Rectangle(
                    (mdates.date2num(center) - width/2, rect_bottom),
                    width, rect_height,
                    facecolor=color, edgecolor='black', linewidth=0.5, alpha=0.8, zorder=3
                ))
                
                # Median line (p50) - horizontal bar within rectangle
                ax_main.plot([mdates.date2num(center) - width/2, mdates.date2num(center) + width/2],
                           [p50[i], p50[i]], color='black', linewidth=1.5, zorder=4)
                
                # Upper whisker (p75 to p90) - thin line with cap
                ax_main.plot([mdates.date2num(center), mdates.date2num(center)],
                           [p75[i], p90[i]], color=color, linewidth=1.0, alpha=0.9, zorder=2)
                # Upper cap
                ax_main.plot([mdates.date2num(center) - width/4, mdates.date2num(center) + width/4],
                           [p90[i], p90[i]], color=color, linewidth=1.0, alpha=0.9, zorder=2)
                
                # Lower whisker (p25 to p10) - thin line with cap
                ax_main.plot([mdates.date2num(center), mdates.date2num(center)],
                           [p25[i], p10[i]], color=color, linewidth=1.0, alpha=0.9, zorder=2)
                # Lower cap
                ax_main.plot([mdates.date2num(center) - width/4, mdates.date2num(center) + width/4],
                           [p10[i], p10[i]], color=color, linewidth=1.0, alpha=0.9, zorder=2)
            
            # Add legend entry for whiskers
            ax_main.plot([], [], 's', color='gray', markersize=8, label='Whisker plots (p10-p90)')
            
            # Optionally show raw points in addition to whiskers
            if show_raw_points:
                ax_main.plot(ts, y_plot, 'o', color="#2E86AB", markersize=markersize*0.5, 
                           alpha=0.2, label="Raw points", zorder=1)
        except Exception as e:
            # Fallback to raw points if aggregation rendering fails
            ax_main.plot(ts, y_plot, label="Corrected Position", color="#2E86AB", 
                       marker="s", markersize=markersize, linewidth=1.5)
    else:
        # No aggregation: plot raw corrected positions
        ax_main.plot(ts, y_plot, label="Corrected Position", color="#2E86AB", 
                   marker="s", markersize=markersize, linewidth=1.5)
    
    # Draw the base fitted line only if no explicit trend overlay is provided
    if trend_center is None:
        ax_main.plot(ts, y_fit_plot, "r--", label="Polynomial Fit", alpha=0.5, linewidth=1.2, zorder=2)
    if str(threshold_mode).lower() == "percentile" and thr_low_plot is not None and thr_high_plot is not None:
        # Asymmetric central interval around median
        label = f"CI {central_confidence*100:.0f}%" if central_confidence else "Central Interval"
        ax_main.fill_between(ts, y_fit_plot + thr_low_plot, y_fit_plot + thr_high_plot, color="gray", alpha=0.18, label=label)
    else:
        if include_sigma_band and np.isfinite(sigma_plot) and float(sigma_plot) > 0:
            ax_main.fill_between(ts, y_fit_plot - sigma_plot, y_fit_plot + sigma_plot, color="gray", alpha=0.2, label="±1σ")
        if include_k_sigma_band and np.isfinite(sigma_plot) and float(sigma_plot) > 0 and np.isfinite(k) and k > 0:
            ax_main.fill_between(ts, y_fit_plot - k * sigma_plot, y_fit_plot + k * sigma_plot, color="gray", alpha=0.08, label=f"±{k:.2f}σ")
    if len(ts) > 0:
        ax_main.scatter([ts[-1]], [y_plot[-1]], color="#D35400", zorder=5, label="Last Point")

    # Overlays: draw after base series, before decorations
    try:
        # Trend overlay: red line + gray band
        if trend_center_plot is not None and trend_center_plot.size == len(ts):
            ax_main.plot(ts, trend_center_plot, color="#C0392B", linewidth=2.2, alpha=0.95, label="Trend", zorder=5)
            if trend_band_lo_plot is not None and trend_band_hi_plot is not None \
               and len(trend_band_lo_plot) == len(ts) and len(trend_band_hi_plot) == len(ts):
                ax_main.fill_between(ts, trend_band_lo_plot, trend_band_hi_plot, color="gray", alpha=0.12, label="Trend range", zorder=1)
        # Decision overlay: green line + green band
        if decision_center_plot is not None and decision_center_plot.size == len(ts):
            ax_main.plot(ts, decision_center_plot, color="#1E8449", linewidth=1.8, alpha=0.9, label="Decision baseline", zorder=4)
            if decision_band_lo_plot is not None and decision_band_hi_plot is not None \
               and len(decision_band_lo_plot) == len(ts) and len(decision_band_hi_plot) == len(ts):
                ax_main.fill_between(ts, decision_band_lo_plot, decision_band_hi_plot, color="#27AE60", alpha=0.10, label="Decision band", zorder=0)
    except Exception:
        pass

    # Titles and labels
    title_extra = []
    if ref_mode:
        title_extra.append(f"ref={ref_mode}")
    if lookback_hours:
        title_extra.append(f"lookback={lookback_hours:.0f}h")
    title_suffix = (" (" + ", ".join(title_extra) + ")") if title_extra else ""
    fig.suptitle(f"Residual Gate Diagnostic - {asset.upper()}" + title_suffix)

    # Set y-axis labels based on mode
    if use_rate:
        ax_main.set_ylabel("Price / Rate")
    elif use_percent:
        ax_main.set_ylabel("Corrected Position (% of base)")
    else:
        ax_main.set_ylabel("Corrected Position (USD)")
    ax_main.grid(True, alpha=0.3, which='major')
    ax_main.grid(True, alpha=0.15, which='minor', linestyle=':')
    # Show reference price baseline (the denominator used for percentage calculations)
    # In percent mode: baseline is at 100% (where position = reference)
    # In absolute mode: baseline is the actual reference price (denom)
    ref_price = 100.0 if use_percent else denom
    ax_main.axhline(ref_price, color="black", linestyle="--", linewidth=1.0, alpha=0.6, label=f"Ref Price ({percent_base})")
    ax_main.minorticks_on()
    # Expand lower bound by 2% to reduce overlap with legend/labels 
    # do this only if the stddev of the data is representing more than 20% of ymax-ymin
    try:
        y0, y1 = ax_main.get_ylim()
        if np.isfinite(y0) and np.isfinite(y1) and (y1 > y0) and (np.std(residuals) > 0.1 * (y1 - y0)):
            if y0 > 0:
                new_low = y0 * 0.98
            else:
                new_low = y0 - 0.02 * (y1 - y0)
            ax_main.set_ylim(new_low, y1)
    except Exception:
        pass
    ax_main.legend(loc="lower right", fontsize=fontsize)
    
    # Optional secondary y-axis for percentage when in absolute mode
    if not use_percent and denom > 0:
        # Use array-compatible mapping functions
        def _fwd(x):
            arr = np.asarray(x, dtype=float)
            return (arr / float(denom)) * 100.0
        def _inv(x):
            arr = np.asarray(x, dtype=float)
            return (arr * float(denom)) / 100.0
        ax_main_pct = ax_main.secondary_yaxis('right', functions=(_fwd, _inv))
        ax_main_pct.set_ylabel('% of base', fontsize=8, color='gray')
        ax_main_pct.tick_params(labelsize=8, colors='gray')

    # Residuals over time
    # Phase E: Apply aggregation to residuals if provided
    if aggregated_data is not None and 'bin_centers' in aggregated_data and 'residuals' in aggregated_data:
        try:
            # Aggregated residuals provided
            bin_centers = np.asarray(aggregated_data['bin_centers'])
            res_stats = aggregated_data['residuals']  # Dict with p10, p25, p50, p75, p90
            
            # Normalize for percent mode
            if use_percent:
                res_stats_norm = {k: (v / denom) * scale if k.startswith('p') else v 
                                  for k, v in res_stats.items()}
            else:
                res_stats_norm = res_stats
            
            # Extract percentiles
            r_p10 = np.asarray(res_stats_norm.get('p10', []))
            r_p25 = np.asarray(res_stats_norm.get('p25', []))
            r_p50 = np.asarray(res_stats_norm.get('p50', []))
            r_p75 = np.asarray(res_stats_norm.get('p75', []))
            r_p90 = np.asarray(res_stats_norm.get('p90', []))
            
            # Determine color (green if p50 > 0, red if < 0)
            colors_res = ['#27AE60' if r_p50[i] >= 0 else '#E74C3C' for i in range(len(bin_centers))]
            
            # Calculate whisker width
            width_hours = 0.3
            if len(bin_centers) > 1:
                dt_seconds = (bin_centers[1] - bin_centers[0]).total_seconds()
                width_hours = dt_seconds / 3600.0 * 0.6
            width = width_hours / 24.0
            
            for i in range(len(bin_centers)):
                center = bin_centers[i]
                color = colors_res[i]
                
                # Main rectangle (p25 to p75)
                rect_bottom = min(r_p25[i], r_p75[i])
                rect_height = abs(r_p75[i] - r_p25[i])
                ax_res.add_patch(plt.Rectangle(
                    (mdates.date2num(center) - width/2, rect_bottom),
                    width, rect_height,
                    facecolor=color, edgecolor='black', linewidth=0.5, alpha=0.8, zorder=3
                ))
                
                # Median line (p50)
                ax_res.plot([mdates.date2num(center) - width/2, mdates.date2num(center) + width/2],
                           [r_p50[i], r_p50[i]], color='black', linewidth=1.5, zorder=4)
                
                # Upper whisker (p75 to p90)
                ax_res.plot([mdates.date2num(center), mdates.date2num(center)],
                           [r_p75[i], r_p90[i]], color=color, linewidth=1.0, alpha=0.9, zorder=2)
                ax_res.plot([mdates.date2num(center) - width/4, mdates.date2num(center) + width/4],
                           [r_p90[i], r_p90[i]], color=color, linewidth=1.0, alpha=0.9, zorder=2)
                
                # Lower whisker (p25 to p10)
                ax_res.plot([mdates.date2num(center), mdates.date2num(center)],
                           [r_p25[i], r_p10[i]], color=color, linewidth=1.0, alpha=0.9, zorder=2)
                ax_res.plot([mdates.date2num(center) - width/4, mdates.date2num(center) + width/4],
                           [r_p10[i], r_p10[i]], color=color, linewidth=1.0, alpha=0.9, zorder=2)
            
            # Show last point
            if len(ts) > 0 and len(r_plot) > 0:
                ax_res.plot([ts[-1]], [r_plot[-1]], 'D', color="#D35400", markersize=markersize*1.5, zorder=5, label="r_now")
            
            # Optionally show raw residuals
            if show_raw_points and len(ts) > 0 and len(r_plot) == len(ts):
                ax_res.plot(ts, r_plot, 'o', color="#346083", markersize=markersize*0.5, alpha=0.2, zorder=1)
        except Exception:
            # Fallback to raw residuals
            if len(ts) > 0 and len(r_plot) == len(ts):
                ax_res.plot(ts, r_plot, color="#346083", linewidth=1.5, markersize=markersize, marker='s')
                ax_res.plot([ts[-1]], [r_plot[-1]], color="#D35400", linestyle='', zorder=5, label="r_now")
    elif len(ts) > 0 and len(r_plot) == len(ts):
        # No aggregation: plot raw residuals
        ax_res.plot(ts, r_plot, color="#346083", linewidth=1.5, markersize=markersize, marker='s')
        ax_res.plot([ts[-1]], [r_plot[-1]], color="#D35400", linestyle='', zorder=5, label="r_now")
    
    ax_res.axhline(0.0, color="black", linestyle="--", linewidth=1.0, alpha=0.6)
    if str(threshold_mode).lower() == "percentile" and thr_low_plot is not None and thr_high_plot is not None:
        ax_res.axhline(thr_low_plot, color="red", linestyle="--", linewidth=1.0, alpha=0.8)
        ax_res.axhline(thr_high_plot, color="red", linestyle="--", linewidth=1.0, alpha=0.8)
    else:
        if np.isfinite(sigma_plot) and float(sigma_plot) > 0:
            ax_res.axhline(+sigma_plot, color="red", linestyle="--", linewidth=1.0, alpha=0.7)
            ax_res.axhline(-sigma_plot, color="red", linestyle="--", linewidth=1.0, alpha=0.7)
            if np.isfinite(k) and k > 0:
                ax_res.axhline(+k * sigma_plot, color="gray", linestyle=":", linewidth=1.0, alpha=0.7)
                ax_res.axhline(-k * sigma_plot, color="gray", linestyle=":", linewidth=1.0, alpha=0.7)
    ax_res.set_xlabel("Time")
    # Set residual y-axis label based on mode
    if use_rate:
        ax_res.set_ylabel("Residual (Rate)")
    elif use_percent:
        ax_res.set_ylabel("Residual (%)")
    else:
        ax_res.set_ylabel("Residual (USD)")
    ax_res.grid(True, alpha=0.3, which='major')
    ax_res.grid(True, alpha=0.15, which='minor', linestyle=':')
    ax_res.minorticks_on()
    # Expand lower bound by 2% for residuals plot as well
    try:
        y0_r, y1_r = ax_res.get_ylim()
        if np.isfinite(y0_r) and np.isfinite(y1_r) and (y1_r > y0_r):
            if y0_r > 0:
                new_low_r = y0_r * 0.98
            else:
                new_low_r = y0_r - 0.02 * (y1_r - y0_r)
            ax_res.set_ylim(new_low_r, y1_r)
    except Exception:
        pass
    
    # Optional secondary y-axis for percentage when in absolute mode
    if not use_percent and denom > 0:
        def _fwd_r(x):
            arr = np.asarray(x, dtype=float)
            return (arr / float(denom)) * 100.0
        def _inv_r(x):
            arr = np.asarray(x, dtype=float)
            return (arr * float(denom)) / 100.0
        ax_res_pct = ax_res.secondary_yaxis('right', functions=(_fwd_r, _inv_r))
        ax_res_pct.set_ylabel('% of base', fontsize=8, color='gray')
        ax_res_pct.tick_params(labelsize=8, colors='gray')
    
    # Add transaction event markers (only for raw and corrected views, not gains_pct)
    if not use_percent:
        # Draw deposit markers (green dashed vertical lines)
        if deposit_timestamps:
            for dep_ts in deposit_timestamps:
                try:
                    xnum = float(mdates.date2num(dep_ts))
                except Exception:
                    continue
                ax_main.axvline(xnum, color='green', linestyle='--', linewidth=1.0, alpha=0.5, zorder=1)
                ax_res.axvline(xnum, color='green', linestyle='--', linewidth=1.0, alpha=0.5, zorder=1)
        
        # Draw withdrawal markers (red dashed vertical lines)
        if withdrawal_timestamps:
            for wth_ts in withdrawal_timestamps:
                try:
                    xnum = float(mdates.date2num(wth_ts))
                except Exception:
                    continue
                ax_main.axvline(xnum, color='red', linestyle='--', linewidth=1.0, alpha=0.5, zorder=1)
                ax_res.axvline(xnum, color='red', linestyle='--', linewidth=1.0, alpha=0.5, zorder=1)
    
    # write model gains per day, month, year on the residuals plot (no boxing), top-right
    # REMOVED: Text is now returned in metrics dict for external display
    # if use_percent:
    #     unit = "%"
    # else:
    #     unit = "USD"
    # textstr = f"Period: {interval_days:.2f}d\nModel Gains:\n  {model_gains_per_month:.2f}{unit}/month  |  {model_gains_per_year:.2f}{unit}/yr"
    # ax_res.text(0.725, 0.97, textstr, transform=ax_res.transAxes, fontsize=6.5, verticalalignment='top')
    
    # Adaptive date formatting based on time range
    if len(ts) > 1:
        time_range_days = interval_days
        
        if time_range_days < 2:
            # Very short range: hourly ticks
            ax_main.xaxis.set_major_locator(mdates.HourLocator(interval=6))
            ax_main.xaxis.set_minor_locator(mdates.HourLocator(interval=1))
            ax_main.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        elif time_range_days < 7:
            # Short range: daily major, 6-hour minor
            ax_main.xaxis.set_major_locator(mdates.DayLocator())
            ax_main.xaxis.set_minor_locator(mdates.HourLocator(interval=6))
            ax_main.xaxis.set_major_formatter(mdates.DateFormatter('%d %b'))
        elif time_range_days < 60:
            # Medium range (your 41-day case): every 3-7 days major, daily minor
            interval = max(3, int(time_range_days / 10))
            ax_main.xaxis.set_major_locator(mdates.DayLocator(interval=interval))
            ax_main.xaxis.set_minor_locator(mdates.DayLocator())
            ax_main.xaxis.set_major_formatter(mdates.DateFormatter('%d %b'))
        elif time_range_days < 365:
            # Long range: weekly/monthly major, weekly minor
            ax_main.xaxis.set_major_locator(mdates.WeekdayLocator(interval=2))
            ax_main.xaxis.set_minor_locator(mdates.WeekdayLocator())
            ax_main.xaxis.set_major_formatter(mdates.DateFormatter('%d %b'))
        else:
            # Very long range: monthly major and minor
            ax_main.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
            ax_main.xaxis.set_minor_locator(mdates.MonthLocator())
            ax_main.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
    else:
        # Fallback for single point or no data
        ax_main.xaxis.set_major_locator(mdates.AutoDateLocator())
        ax_main.xaxis.set_major_formatter(mdates.ConciseDateFormatter(ax_main.xaxis.get_major_locator()))
    
    fig.autofmt_xdate()

    # Residual distribution
    r_clean = r_plot[np.isfinite(r_plot)] if r_plot.size else r_plot
    n = int(r_clean.size)
    if n <= 0:
        bins = 5
    else:
        bsz = max(1, hist_samples_per_bin)
        bins = int(max(5, min(100, round(n / bsz))))
    if n > 0:
        ax_pdf.hist(r_clean, bins=bins, color="#3498DB", alpha=0.6, density=True, edgecolor="white")
        # Optional KDE (no external deps)
        try:
            grid = np.linspace(np.min(r_clean), np.max(r_clean), 200)
            kde = _gaussian_kde(grid, r_clean)
            if kde is not None:
                ax_pdf.plot(grid, kde, color="#1B4F72", linewidth=1.5, label="KDE")
        except Exception:
            pass
        # Markers
        ax_pdf.axvline(0.0, color="black", linewidth=1.0, alpha=0.6)
        if str(threshold_mode).lower() == "percentile" and thr_low_plot is not None and thr_high_plot is not None:
            ax_pdf.axvline(thr_low_plot, color="red", linestyle="--", linewidth=1.0, alpha=0.8)
            ax_pdf.axvline(thr_high_plot, color="red", linestyle="--", linewidth=1.0, alpha=0.8)
        else:
            if np.isfinite(sigma_plot) and float(sigma_plot) > 0:
                ax_pdf.axvline(+sigma_plot, color="red", linestyle="--", linewidth=1.0, alpha=0.7)
                ax_pdf.axvline(-sigma_plot, color="red", linestyle="--", linewidth=1.0, alpha=0.7)
                if np.isfinite(k) and k > 0:
                    ax_pdf.axvline(+k * sigma_plot, color="gray", linestyle=":", linewidth=1.0, alpha=0.7)
                    ax_pdf.axvline(-k * sigma_plot, color="gray", linestyle=":", linewidth=1.0, alpha=0.7)
        if np.isfinite(residual_now_plot):
            ax_pdf.axvline(residual_now_plot, color="#D35400", linewidth=2.0, alpha=0.9, label="r_now")
    ax_pdf.set_title("Residual Distribution")
    # Set residual distribution x-axis label based on mode
    if use_rate:
        ax_pdf.set_xlabel("Residual (Rate)")
    elif use_percent:
        ax_pdf.set_xlabel("Residual (%)")
    else:
        ax_pdf.set_xlabel("Residual (USD)")
    ax_pdf.set_ylabel("Density")
    ax_pdf.grid(True, alpha=0.3, which='major')
    ax_pdf.grid(True, alpha=0.15, which='minor', linestyle=':', axis='x')
    ax_pdf.minorticks_on()
    ax_pdf.legend(loc="best", fontsize=fontsize)


    plt.tight_layout(rect=(0.0, 0.0, 1.0, 0.96))

    # Output path
    out_base = Path(out_dir)
    out_base.mkdir(parents=True, exist_ok=True)
    suffix = ts[-1].strftime("%Y-%m-%dT%H-%M-%SZ") if len(ts) > 0 else "now"
    filename = f"{asset.lower()}_{ref_mode or 'noref'}_{suffix}.png"
    out_path = out_base / filename
    plt.savefig(str(out_path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    
    return str(out_path)
