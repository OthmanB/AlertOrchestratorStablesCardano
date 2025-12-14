#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prometheus exporter using prometheus_client honoring telemetry.expose toggles.
"""
from __future__ import annotations

from typing import Optional, Dict, List
from dataclasses import dataclass, asdict
from datetime import datetime
from prometheus_client import Gauge, CollectorRegistry, generate_latest, CONTENT_TYPE_LATEST
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import time
import os
import json
from urllib.parse import urlparse, parse_qs, urlencode
import base64
import hmac
import logging

from .settings import Settings
from ..shared.greptime_reader import GreptimeReader
from ..shared.greptime_writer import GreptimeWriter
from ..shared.liqwid_client import LiqwidClient
from .transaction_syncer import TransactionSyncer
from ..shared.correct_calculations import (
    calculate_correct_gains,
    create_unified_timebase,
    interpolate_positions_on_timebase,
    create_transaction_vectors_on_timebase,
)
from ..shared.resolver import Resolver
from .diagnostics import plot_residual_composite
from .config_normalizer import build_normalized_config
from .alert_logic import AssetDecision
from ..shared.config import DateRange


@dataclass
class MetricHandles:
    decision: Optional[Gauge] = None
    wmax_usd: Optional[Gauge] = None
    v_ref_usd: Optional[Gauge] = None
    v_t1_usd: Optional[Gauge] = None
    g_usd: Optional[Gauge] = None
    price_t1_usd: Optional[Gauge] = None
    t0_timestamp_seconds: Optional[Gauge] = None
    t1_timestamp_seconds: Optional[Gauge] = None
    residual_usd: Optional[Gauge] = None
    sigma_usd: Optional[Gauge] = None
    k_sigma: Optional[Gauge] = None
    residual_trigger: Optional[Gauge] = None
    last_eval_timestamp_seconds: Optional[Gauge] = None
    # Phase C price compare
    price_usd: Optional[Gauge] = None  # labeled by source
    price_delta_abs: Optional[Gauge] = None
    price_delta_rel: Optional[Gauge] = None
    price_mismatch: Optional[Gauge] = None
    price_compare_unavailable: Optional[Gauge] = None
    # Rate metrics
    rate_usd: Optional[Gauge] = None  # labeled by asset, alternate_asset_name and source
    rate_ada: Optional[Gauge] = None  # labeled by asset, alternate_asset_name and source (minswap via greptime)


class MetricsExporter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.registry = CollectorRegistry()
        pfx = settings.orchestrator.telemetry.metric_prefix
        exp = settings.orchestrator.telemetry.expose
        self.latest_decisions: Dict[str, AssetDecision] = {}
        self._resolver = None  # lazy-initialized Resolver
        self._greptime_reader = None  # lazy-initialized GreptimeReader for Resolver
        # Phase-1: build normalized config view (no behavior change intended)
        try:
            self._norm = build_normalized_config(settings)
        except Exception:
            self._norm = None
        
        # Phase B: Plot range control state management
        # Stores per-asset plot range overrides from dashboard UI
        # Format: {asset: {'start': datetime, 'end': datetime, 'gains_reference': 'plot_range'|'alert_driven'}}
        self._plot_range_overrides: Dict[str, dict] = {}
        # Stores expanded data ranges when plot range exceeds sync range
        # Format: {asset: DateRange}
        self._expanded_data_ranges: Dict[str, DateRange] = {}

        # Phase E: Aggregation control state management
        # Stores per-asset aggregation settings from dashboard UI
        # Format: {asset: {'enabled': bool, 'time_unit': str, 'show_raw_points': bool}}
        self._aggregation_overrides: Dict[str, dict] = {}

        # Create metrics conditionally to avoid exposing unwanted names
        self.metrics = MetricHandles(
            decision=Gauge(f"{pfx}decision", "Decision: 1=WITHDRAW_OK, 0=HOLD, -1=ERROR", ['asset', 'alternate_asset_name', 'ref_mode'], registry=self.registry) if exp.decision else None,
            wmax_usd=Gauge(f"{pfx}wmax_usd", "Max allowable withdrawal in USD", ['asset', 'alternate_asset_name', 'ref_mode'], registry=self.registry) if exp.wmax_usd else None,
            v_ref_usd=Gauge(f"{pfx}v_ref_usd", "Reference V_i(t0) raw position (USD)", ['asset', 'alternate_asset_name', 'ref_mode'], registry=self.registry) if exp.v_ref_usd else None,
            v_t1_usd=Gauge(f"{pfx}v_t1_usd", "Latest V_i(t1) raw position (USD)", ['asset', 'alternate_asset_name', 'ref_mode'], registry=self.registry) if exp.v_t1_usd else None,
            g_usd=Gauge(f"{pfx}g_usd", "Corrected gain since t0 (USD)", ['asset', 'alternate_asset_name', 'ref_mode'], registry=self.registry) if exp.g_usd else None,
            price_t1_usd=Gauge(f"{pfx}price_t1_usd", "Price at t1 (USD)", ['asset', 'alternate_asset_name', 'ref_mode'], registry=self.registry) if exp.price_t1_usd else None,
            t0_timestamp_seconds=Gauge(f"{pfx}t0_timestamp_seconds", "Reference timestamp (epoch seconds)", ['asset', 'alternate_asset_name', 'ref_mode'], registry=self.registry) if exp.t0_timestamp_seconds else None,
            t1_timestamp_seconds=Gauge(f"{pfx}t1_timestamp_seconds", "Latest timestamp (epoch seconds)", ['asset', 'alternate_asset_name', 'ref_mode'], registry=self.registry) if exp.t1_timestamp_seconds else None,
            residual_usd=Gauge(f"{pfx}residual_usd", "Residual at t1 (USD)", ['asset', 'alternate_asset_name', 'ref_mode'], registry=self.registry) if exp.residual_usd else None,
            sigma_usd=Gauge(f"{pfx}sigma_usd", "Residual sigma (USD)", ['asset', 'alternate_asset_name', 'ref_mode'], registry=self.registry) if exp.sigma_usd else None,
            k_sigma=Gauge(f"{pfx}k_sigma", "K-sigma threshold", ['asset', 'alternate_asset_name', 'ref_mode'], registry=self.registry) if exp.k_sigma else None,
            residual_trigger=Gauge(f"{pfx}residual_trigger", "Residual trigger flag (0/1)", ['asset', 'alternate_asset_name', 'ref_mode'], registry=self.registry) if exp.residual_trigger else None,
            last_eval_timestamp_seconds=Gauge(f"{pfx}last_eval_timestamp_seconds", "Last evaluation timestamp (epoch seconds)", registry=self.registry),
            price_usd=Gauge(f"{pfx}price_usd", "Price by source (USD)", ['asset', 'alternate_asset_name', 'source'], registry=self.registry) if exp.price_usd_by_source else None,
            price_delta_abs=Gauge(f"{pfx}price_delta_abs", "Abs price delta (USD)", ['asset', 'alternate_asset_name'], registry=self.registry) if exp.price_delta_abs else None,
            price_delta_rel=Gauge(f"{pfx}price_delta_rel", "Relative price delta", ['asset', 'alternate_asset_name'], registry=self.registry) if exp.price_delta_rel else None,
            price_mismatch=Gauge(f"{pfx}price_mismatch", "Price mismatch flag (0/1)", ['asset', 'alternate_asset_name'], registry=self.registry) if exp.price_mismatch else None,
            price_compare_unavailable=Gauge(f"{pfx}price_compare_unavailable", "Price compare unavailable (0/1)", ['asset', 'alternate_asset_name'], registry=self.registry) if exp.price_compare_unavailable else None,
            rate_usd=Gauge(f"{pfx}rate_usd", "USD rate time series (price_usd)", ['asset', 'alternate_asset_name', 'source'], registry=self.registry) if exp.rate_usd else None,
            rate_ada=Gauge(f"{pfx}rate_ada", "ADA rate time series (price_usd/ada_usd, minswap)", ['asset', 'alternate_asset_name', 'source'], registry=self.registry) if exp.rate_ada else None,
        )

        self._metrics_payload_lock = threading.Lock()
        try:
            self._metrics_payload = generate_latest(self.registry)
        except Exception:
            self._metrics_payload = b""

    def start_http(self) -> None:
        """Start an HTTP server exposing /metrics, /api endpoints, and the dashboard."""
        tel = self.settings.orchestrator.telemetry
        outer_self = self

        class Handler(BaseHTTPRequestHandler):  # type: ignore
            def log_message(self, format: str, *args) -> None:  # quiet logs
                try:
                    return
                except Exception:
                    pass

            def do_GET(self_inner):  # type: ignore
                try:
                    parsed = urlparse(self_inner.path)
                    path = parsed.path

                    # /metrics
                    if path == tel.path:
                        try:
                            with outer_self._metrics_payload_lock:
                                output = outer_self._metrics_payload
                        except Exception:
                            output = generate_latest(outer_self.registry)
                        self_inner.send_response(200)
                        self_inner.send_header("Content-Type", CONTENT_TYPE_LATEST)
                        self_inner.send_header("Content-Length", str(len(output)))
                        self_inner.end_headers()
                        self_inner.wfile.write(output)
                        return

                    # /api/assets
                    if path == "/api/assets":
                        assets = outer_self._get_assets_alpha()
                        sel = outer_self._select_asset(parse_qs(parsed.query))
                        assets_payload = {"assets": assets, "selected": sel}
                        data = json.dumps(assets_payload).encode("utf-8")
                        self_inner.send_response(200)
                        self_inner.send_header("Content-Type", "application/json")
                        self_inner.send_header("Content-Length", str(len(data)))
                        self_inner.end_headers()
                        self_inner.wfile.write(data)
                        return

                    # /api/decisions
                    if path == "/api/decisions":
                        is_authenticated = outer_self._is_authenticated(self_inner.headers)
                        decisions_payload: Dict[str, dict] = {}
                        for asset, dec in (outer_self.latest_decisions or {}).items():
                            wallet_breakdown_data = []
                            total_wmax = 0.0
                            if getattr(dec, 'wmax_usd', None):
                                for wb in dec.wmax_usd:
                                    if is_authenticated:
                                        wallet_breakdown_data.append({
                                            "wallet_address": wb.wallet_address,
                                            "abbreviated_address": wb.abbreviated_address(),
                                            "wmax_usd": wb.wmax_usd,
                                            "v_t1_usd": getattr(wb, 'v_t1_usd', None),
                                        })
                                    total_wmax += float(wb.wmax_usd)
                            decisions_payload[asset] = {
                                "decision": dec.decision,
                                "ref_mode": dec.ref_mode,
                                "wmax_usd": total_wmax,
                                "wallet_breakdown": wallet_breakdown_data,
                                "residual_usd": dec.residual_usd,
                                "sigma_usd": dec.sigma_usd,
                                "k_sigma": dec.k_sigma,
                                "residual_trigger": dec.residual_trigger,
                                "debug_plot_path": dec.debug_plot_path,
                                "t0_ts": dec.t0_timestamp_seconds,
                                "t1_ts": dec.t1_timestamp_seconds,
                                "prices_by_source": getattr(dec, 'prices_by_source', {}),
                                "price_delta_abs": getattr(dec, 'price_delta_abs', None),
                                "price_delta_rel": getattr(dec, 'price_delta_rel', None),
                                "price_mismatch": getattr(dec, 'price_mismatch', None),
                                "price_compare_unavailable": getattr(dec, 'price_compare_unavailable', None),
                            }
                        data = json.dumps(decisions_payload).encode("utf-8")
                        self_inner.send_response(200)
                        self_inner.send_header("Content-Type", "application/json")
                        self_inner.send_header("Content-Length", str(len(data)))
                        self_inner.end_headers()
                        self_inner.wfile.write(data)
                        return

                    # /api/config/normalized (config doctor)
                    if path == "/api/config/normalized":
                        # Require auth if configured/enabled
                        try:
                            cfg_auth = getattr(outer_self.settings.orchestrator, 'auth', None)
                            if cfg_auth and getattr(cfg_auth, 'enabled', False):
                                if not outer_self._check_basic_auth(self_inner):
                                    return  # challenge sent
                        except Exception:
                            pass
                        # Build normalized config (reuse cached when available)
                        try:
                            norm = getattr(outer_self, '_norm', None)
                            if norm is None:
                                norm = build_normalized_config(outer_self.settings)
                        except Exception as e:
                            err = {"error": f"failed to build normalized config: {e}"}
                            data = json.dumps(err).encode("utf-8")
                            self_inner.send_response(500)
                            self_inner.send_header("Content-Type", "application/json")
                            self_inner.send_header("Content-Length", str(len(data)))
                            self_inner.end_headers()
                            self_inner.wfile.write(data)
                            return
                        payload = asdict(norm)
                        data = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
                        self_inner.send_response(200)
                        self_inner.send_header("Content-Type", "application/json")
                        self_inner.send_header("Content-Length", str(len(data)))
                        self_inner.end_headers()
                        self_inner.wfile.write(data)
                        return

                    # /dashboard
                    if path == "/dashboard":
                        q = parse_qs(parsed.query)
                        asset = outer_self._select_asset(q)
                        view = (q.get("view", ["gains_pct"])[0] or "gains_pct").strip().lower()
                        if view not in ("gains_pct", "corrected", "raw", "rate_usd", "rate_ada"):
                            view = "gains_pct"

                        # Source selection and banner for invalid source
                        # Prefer normalized providers when available
                        try:
                            norm = getattr(outer_self, '_norm', None)
                            prices = getattr(norm, 'prices', None) if norm is not None else None
                            prov = []
                            if prices is not None:
                                logicals = list(getattr(prices, 'logical_sources', []) or [])
                                prio = dict(getattr(prices, 'priority_by_logical', {}) or {})
                                seen = set()
                                keys = list(logicals) + [k for k in prio.keys() if k not in logicals]
                                for k in keys:
                                    for p in (prio.get(k, []) or []):
                                        ps = str(p).strip()
                                        if ps.lower().startswith('greptime(') and ps not in seen:
                                            prov.append(ps); seen.add(ps)
                            configured_sources = prov or ["greptime(liqwid)"]
                            default_source = (prov[0] if prov else "greptime(liqwid)")
                        except Exception:
                            configured_sources = ["greptime(liqwid)"]
                            default_source = "greptime(liqwid)"
                        src_q = (q.get("source", [default_source])[0] or default_source).strip()
                        banner = None
                        # ADA rate view is locked to Minswap (ada_usd)
                        if view == "rate_ada":
                            src_q = "greptime(minswap)"  # enforced
                            banner = "ADA rate view is locked to Minswap (ada_usd)."
                            configured_sources = ["greptime(minswap)"]
                            default_source = "greptime(minswap)"
                        if src_q not in configured_sources:
                            banner = f"Invalid source '{src_q}' corrected to '{default_source}'."
                            src_q = default_source if default_source in configured_sources else (configured_sources[0] if configured_sources else "greptime(liqwid)")

                        # Private views require basic auth
                        if view in ("corrected", "raw"):
                            if not outer_self._check_basic_auth(self_inner):
                                return  # challenge already sent

                        # Check if user is authenticated (for UI control states)
                        is_authenticated = outer_self._is_authenticated(self_inner.headers)

                        html = outer_self._render_dashboard_html(asset, view, src_q, banner, is_authenticated)
                        data = html.encode("utf-8")
                        self_inner.send_response(200)
                        self_inner.send_header("Content-Type", "text/html; charset=utf-8")
                        self_inner.send_header("Content-Length", str(len(data)))
                        self_inner.end_headers()
                        self_inner.wfile.write(data)
                        return

                    # Fallback 404
                    self_inner.send_response(404)
                    self_inner.end_headers()
                except Exception:
                    try:
                        self_inner.send_response(500)
                        self_inner.end_headers()
                    except Exception:
                        pass

            def do_POST(self_inner):  # type: ignore
                """Handle POST requests for transaction sync API"""
                try:
                    parsed = urlparse(self_inner.path)
                    path = parsed.path

                    # POST /api/sync-transactions
                    if path == "/api/sync-transactions":
                        # Require auth if configured
                        try:
                            cfg_auth = getattr(outer_self.settings.orchestrator, 'auth', None)
                            if cfg_auth and getattr(cfg_auth, 'enabled', False):
                                if not outer_self._check_basic_auth(self_inner):
                                    return  # challenge sent
                        except Exception:
                            pass

                        # Read request body (if any)
                        content_length = int(self_inner.headers.get('Content-Length', 0))
                        request_body = {}
                        if content_length > 0:
                            body_bytes = self_inner.rfile.read(content_length)
                            try:
                                request_body = json.loads(body_bytes.decode('utf-8'))
                            except Exception:
                                pass

                        # Handle sync request
                        response_data, content_type = outer_self._handle_sync_transactions(request_body)

                        self_inner.send_response(200)
                        self_inner.send_header("Content-Type", content_type)
                        self_inner.send_header("Content-Length", str(len(response_data)))
                        self_inner.end_headers()
                        self_inner.wfile.write(response_data)
                        return

                    # POST /api/update-plot-range
                    if path == "/api/update-plot-range":
                        # Read request body
                        content_length = int(self_inner.headers.get('Content-Length', 0))
                        if content_length == 0:
                            error_data = json.dumps({'success': False, 'error': 'Request body required'}).encode('utf-8')
                            self_inner.send_response(400)
                            self_inner.send_header("Content-Type", "application/json")
                            self_inner.send_header("Content-Length", str(len(error_data)))
                            self_inner.end_headers()
                            self_inner.wfile.write(error_data)
                            return
                        
                        body_bytes = self_inner.rfile.read(content_length)
                        try:
                            body = json.loads(body_bytes.decode('utf-8'))
                        except Exception as e:
                            error_data = json.dumps({'success': False, 'error': f'Invalid JSON: {e}'}).encode('utf-8')
                            self_inner.send_response(400)
                            self_inner.send_header("Content-Type", "application/json")
                            self_inner.send_header("Content-Length", str(len(error_data)))
                            self_inner.end_headers()
                            self_inner.wfile.write(error_data)
                            return
                        
                        # Extract parameters
                        asset = body.get('asset')
                        start = body.get('start')
                        end = body.get('end')
                        gains_ref = body.get('gains_reference', 'alert_driven')
                        
                        if not asset or not start or not end:
                            error_data = json.dumps({'success': False, 'error': 'Missing required fields: asset, start, end'}).encode('utf-8')
                            self_inner.send_response(400)
                            self_inner.send_header("Content-Type", "application/json")
                            self_inner.send_header("Content-Length", str(len(error_data)))
                            self_inner.end_headers()
                            self_inner.wfile.write(error_data)
                            return
                        
                        # Parse datetimes
                        try:
                            start_dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
                            end_dt = datetime.fromisoformat(end.replace('Z', '+00:00'))
                        except Exception as e:
                            error_data = json.dumps({'success': False, 'error': f'Invalid datetime format: {e}'}).encode('utf-8')
                            self_inner.send_response(400)
                            self_inner.send_header("Content-Type", "application/json")
                            self_inner.send_header("Content-Length", str(len(error_data)))
                            self_inner.end_headers()
                            self_inner.wfile.write(error_data)
                            return
                        
                        # Update plot range override
                        outer_self._plot_range_overrides[asset] = {
                            'start': start_dt,
                            'end': end_dt,
                            'gains_reference': gains_ref
                        }
                        
                        # Check if requested range exceeds data range
                        data_range = outer_self.settings.client.date_range
                        data_refresh_triggered = False
                        if (data_range.start and start_dt < data_range.start) or \
                           (data_range.end and end_dt > data_range.end):
                            # Trigger background data refresh
                            outer_self._schedule_data_refresh(asset, start, end)
                            data_refresh_triggered = True
                        
                        # Response
                        response = {
                            'success': True,
                            'data_refresh_triggered': data_refresh_triggered,
                            'effective_range': {
                                'start': start,
                                'end': end
                            },
                            'gains_reference': gains_ref
                        }
                        if data_refresh_triggered:
                            response['message'] = "Requested range exceeds synced data. Background refresh started."
                            response['eta_seconds'] = 30
                        
                        data = json.dumps(response).encode('utf-8')
                        self_inner.send_response(200)
                        self_inner.send_header("Content-Type", "application/json")
                        self_inner.send_header("Content-Length", str(len(data)))
                        self_inner.end_headers()
                        self_inner.wfile.write(data)
                        return
                    
                    # POST /api/update-aggregation (Phase E)
                    if path == "/api/update-aggregation":
                        # Read request body
                        content_length = int(self_inner.headers.get('Content-Length', 0))
                        if content_length == 0:
                            error_data = json.dumps({'success': False, 'error': 'Request body required'}).encode('utf-8')
                            self_inner.send_response(400)
                            self_inner.send_header("Content-Type", "application/json")
                            self_inner.send_header("Content-Length", str(len(error_data)))
                            self_inner.end_headers()
                            self_inner.wfile.write(error_data)
                            return
                        
                        body_bytes = self_inner.rfile.read(content_length)
                        try:
                            body = json.loads(body_bytes.decode('utf-8'))
                        except Exception as e:
                            error_data = json.dumps({'success': False, 'error': f'Invalid JSON: {e}'}).encode('utf-8')
                            self_inner.send_response(400)
                            self_inner.send_header("Content-Type", "application/json")
                            self_inner.send_header("Content-Length", str(len(error_data)))
                            self_inner.end_headers()
                            self_inner.wfile.write(error_data)
                            return
                        
                        # Extract parameters
                        asset = body.get('asset')
                        enabled = body.get('enabled', False)
                        time_unit = body.get('time_unit', '5min')
                        show_raw_points = body.get('show_raw_points', True)
                        
                        if not asset:
                            error_data = json.dumps({'success': False, 'error': 'Missing required field: asset'}).encode('utf-8')
                            self_inner.send_response(400)
                            self_inner.send_header("Content-Type", "application/json")
                            self_inner.send_header("Content-Length", str(len(error_data)))
                            self_inner.end_headers()
                            self_inner.wfile.write(error_data)
                            return
                        
                        # Validate time_unit
                        valid_units = ['1min', '5min', '15min', '1h', '6h', '1d']
                        if time_unit not in valid_units:
                            error_data = json.dumps({'success': False, 'error': f'Invalid time_unit. Must be one of: {", ".join(valid_units)}'}).encode('utf-8')
                            self_inner.send_response(400)
                            self_inner.send_header("Content-Type", "application/json")
                            self_inner.send_header("Content-Length", str(len(error_data)))
                            self_inner.end_headers()
                            self_inner.wfile.write(error_data)
                            return
                        
                        # Update aggregation override
                        outer_self._aggregation_overrides[asset] = {
                            'enabled': bool(enabled),
                            'time_unit': time_unit,
                            'show_raw_points': bool(show_raw_points)
                        }
                        
                        # Response
                        response = {
                            'success': True,
                            'aggregation': {
                                'enabled': bool(enabled),
                                'time_unit': time_unit,
                                'show_raw_points': bool(show_raw_points)
                            }
                        }
                        
                        data = json.dumps(response).encode('utf-8')
                        self_inner.send_response(200)
                        self_inner.send_header("Content-Type", "application/json")
                        self_inner.send_header("Content-Length", str(len(data)))
                        self_inner.end_headers()
                        self_inner.wfile.write(data)
                        return

                    # Fallback 404
                    self_inner.send_response(404)
                    self_inner.end_headers()
                except Exception as e:
                    try:
                        error_data = json.dumps({'success': False, 'error': str(e)}).encode('utf-8')
                        self_inner.send_response(500)
                        self_inner.send_header("Content-Type", "application/json")
                        self_inner.send_header("Content-Length", str(len(error_data)))
                        self_inner.end_headers()
                        self_inner.wfile.write(error_data)
                    except Exception:
                        pass

        # Start server in background
        server = HTTPServer((tel.listen_address, int(tel.listen_port)), Handler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()

    def _handle_sync_transactions(self, request_body: Dict) -> tuple[bytes, str]:
        """
        Handle POST /api/sync-transactions
        Sync all wallets with Liqwid historical transactions.
        
        Automatically discovers wallet addresses from existing supply position data.
        Falls back to configured wallets if discovery yields no results.
        
        Args:
            request_body: Request JSON with optional date range:
                {
                    "start_date": "2023-01-01T00:00:00.000Z",  # Optional
                    "end_date": "2024-12-31T23:59:59.999Z"     # Optional
                }
        
        Returns:
            Tuple of (response_body, content_type)
        """
        logger = logging.getLogger(__name__)
        
        # Extract optional date range from request body OR fall back to config defaults
        start_date = request_body.get('start_date') if request_body else None
        end_date = request_body.get('end_date') if request_body else None
        
        # Fallback to config defaults if not provided in request
        if not start_date:
            start_date = self.settings.orchestrator.transaction_sync.start_date
        if not end_date:
            end_date = self.settings.orchestrator.transaction_sync.end_date
        
        # Log source of date range
        if request_body and (request_body.get('start_date') or request_body.get('end_date')):
            logger.info(f"Using dashboard-provided date range: {start_date} to {end_date or 'now'}")
        elif start_date or end_date:
            logger.info(f"Using config default date range: {start_date} to {end_date or 'now'}")
        else:
            logger.info("Using hardcoded fallback date range (Oct 2023 - now)")
        
        try:
            # Initialize reader for wallet discovery using helper function
            from ..shared.greptime_reader import create_greptime_reader
            
            # Get table prefix from settings for wallet discovery
            table_prefix = self.settings.client.table_asset_prefix
            
            logger.info(f"Discovering wallet addresses from tables with prefix '{table_prefix}'...")
            
            # Create reader for wallet discovery
            discovery_reader = create_greptime_reader(
                config=self.settings.client.greptime,
                table_prefix=table_prefix
            )
            
            # Discover wallet addresses from existing data
            wallets = discovery_reader.discover_wallet_addresses()
            
            # Fallback to configured wallets if discovery found nothing
            if not wallets:
                logger.warning("No wallets discovered from data, checking configuration...")
                wallets = getattr(self.settings.orchestrator, 'wallets', [])
            
            if not wallets:
                return (
                    json.dumps({
                        'success': False,
                        'error': 'No wallet addresses found in data or configuration'
                    }).encode(),
                    'application/json'
                )
            
            # Get Liqwid GraphQL endpoint (handle Optional[ApisConfig])
            apis_cfg = self.settings.orchestrator.apis
            liqwid_endpoint = getattr(apis_cfg, 'liqwid_graphql', None) if apis_cfg is not None else None
            if not liqwid_endpoint:
                return (
                    json.dumps({
                        'success': False,
                        'error': 'Liqwid GraphQL endpoint not configured'
                    }).encode(),
                    'application/json'
                )
            
            logger.info(f"Starting sync for {len(wallets)} wallet(s)")
            
            # Initialize clients and syncer using helper function for reader
            
            liqwid_client = LiqwidClient(
                endpoint=liqwid_endpoint,
                timeout=30,
                retry_attempts=3,
                retry_backoff=5
            )
            
            greptime_reader = create_greptime_reader(
                config=self.settings.client.greptime,
                table_prefix=self.settings.client.table_asset_prefix
            )
            
            # Check if test_prefix is enabled in settings
            test_prefix = getattr(self.settings.client.greptime, 'test_prefix', False)
            
            greptime_writer = GreptimeWriter(
                config=self.settings.client.greptime,
                deposits_prefix=self.settings.client.deposits_prefix,
                withdrawals_prefix=self.settings.client.withdrawals_prefix,
                test_prefix=test_prefix
            )
            
            # Get reference keyword from config for transaction notes
            # This ensures synced transactions include the keyword for reference detection
            # in gains calculation (analysis.decision.reference.keyword)
            reference_keyword = self.settings.orchestrator.reference_keyword
            
            syncer = TransactionSyncer(
                liqwid_client=liqwid_client,
                greptime_reader=greptime_reader,
                greptime_writer=greptime_writer,
                logger=logger,
                reference_keyword=reference_keyword
            )
            
            # Sync each wallet and collect reports
            total_new = 0
            total_skipped = 0
            total_deposits = 0
            total_withdrawals = 0
            all_errors = []
            
            for wallet in wallets:
                logger.info(f"Syncing wallet {wallet[:20]}...")
                report = syncer.sync_wallet(
                    wallet_address=wallet,
                    assets=self.settings.client.assets,
                    start_date=start_date,
                    end_date=end_date
                )
                
                total_new += report.new_deposits + report.new_withdrawals
                total_skipped += report.skipped_duplicates
                total_deposits += report.new_deposits
                total_withdrawals += report.new_withdrawals
                if report.errors:
                    all_errors.extend(report.errors)
            
            logger.info(f"Sync complete: {total_new} new transactions, {total_skipped} skipped")
            
            return (
                json.dumps({
                    'success': len(all_errors) == 0,
                    'wallets_synced': len(wallets),
                    'new_transactions': total_new,
                    'duplicates_skipped': total_skipped,
                    'deposits_written': total_deposits,
                    'withdrawals_written': total_withdrawals,
                    'errors': [str(e) for e in all_errors]
                }, indent=2).encode(),
                'application/json'
            )
            
        except Exception as e:
            logger.error(f"Sync failed: {e}", exc_info=True)
            return (
                json.dumps({
                    'success': False,
                    'error': str(e)
                }).encode(),
                'application/json'
            )

    def update(self, decisions: Dict[str, AssetDecision]) -> None:
        """Update Prometheus metrics and snapshot latest decisions for the dashboard."""
        try:
            # Update last evaluation time
            if self.metrics.last_eval_timestamp_seconds is not None:
                self.metrics.last_eval_timestamp_seconds.set(time.time())

            # Keep a copy for dashboard endpoints
            self.latest_decisions = dict(decisions or {})
            # Lazily initialize Resolver for alternate asset naming
            if self._resolver is None:
                try:
                    self._greptime_reader = GreptimeReader(self.settings.client.greptime, self.settings.client.table_asset_prefix)
                    self._resolver = Resolver(greptime_reader=self._greptime_reader)
                except Exception:
                    self._resolver = None
            def _resolved_symbol(name: str) -> str:
                n = (name or '').strip()
                if not n:
                    return n
                try:
                    if self._resolver is not None:
                        _mid, sym = self._resolver.resolve_asset(n)
                        return str(sym).strip().lower() or n
                except Exception:
                    pass
                if n.lower() in ('usdc', 'usdt'):
                    return 'wan' + n.lower()
                return n.lower()

            for asset, dec in (decisions or {}).items():
                ref_mode = (dec.ref_mode or "unknown")
                alt = _resolved_symbol(asset)
                alt_label = "" if (alt == asset.lower()) else alt
                if self.metrics.decision is not None:
                    self.metrics.decision.labels(asset=asset, alternate_asset_name=alt_label, ref_mode=ref_mode).set(dec.decision)
                if self.metrics.wmax_usd is not None and getattr(dec, 'wmax_usd', None) is not None:
                    total_wmax = sum(wb.wmax_usd for wb in dec.wmax_usd)
                    self.metrics.wmax_usd.labels(asset=asset, alternate_asset_name=alt_label, ref_mode=ref_mode).set(total_wmax)
                if self.metrics.v_ref_usd is not None:
                    v = getattr(dec, 'v_ref_usd', None)
                    if v is not None:
                        self.metrics.v_ref_usd.labels(asset=asset, alternate_asset_name=alt_label, ref_mode=ref_mode).set(float(v))
                if self.metrics.v_t1_usd is not None:
                    v = getattr(dec, 'v_t1_usd', None)
                    if v is not None:
                        self.metrics.v_t1_usd.labels(asset=asset, alternate_asset_name=alt_label, ref_mode=ref_mode).set(float(v))
                if self.metrics.g_usd is not None:
                    v = getattr(dec, 'g_usd', None)
                    if v is not None:
                        self.metrics.g_usd.labels(asset=asset, alternate_asset_name=alt_label, ref_mode=ref_mode).set(float(v))
                if self.metrics.price_t1_usd is not None:
                    v = getattr(dec, 'price_t1_usd', None)
                    if v is not None:
                        self.metrics.price_t1_usd.labels(asset=asset, alternate_asset_name=alt_label, ref_mode=ref_mode).set(float(v))
                if self.metrics.t0_timestamp_seconds is not None:
                    v = getattr(dec, 't0_timestamp_seconds', None)
                    if v is not None:
                        self.metrics.t0_timestamp_seconds.labels(asset=asset, alternate_asset_name=alt_label, ref_mode=ref_mode).set(float(v))
                if self.metrics.t1_timestamp_seconds is not None:
                    v = getattr(dec, 't1_timestamp_seconds', None)
                    if v is not None:
                        self.metrics.t1_timestamp_seconds.labels(asset=asset, alternate_asset_name=alt_label, ref_mode=ref_mode).set(float(v))
                if self.metrics.residual_usd is not None:
                    v = getattr(dec, 'residual_usd', None)
                    if v is not None:
                        self.metrics.residual_usd.labels(asset=asset, alternate_asset_name=alt_label, ref_mode=ref_mode).set(float(v))
                if self.metrics.sigma_usd is not None:
                    v = getattr(dec, 'sigma_usd', None)
                    if v is not None:
                        self.metrics.sigma_usd.labels(asset=asset, alternate_asset_name=alt_label, ref_mode=ref_mode).set(float(v))
                if self.metrics.k_sigma is not None:
                    v = getattr(dec, 'k_sigma', None)
                    if v is not None:
                        self.metrics.k_sigma.labels(asset=asset, alternate_asset_name=alt_label, ref_mode=ref_mode).set(float(v))
                if self.metrics.residual_trigger is not None:
                    v = getattr(dec, 'residual_trigger', None)
                    if v is not None:
                        self.metrics.residual_trigger.labels(asset=asset, alternate_asset_name=alt_label, ref_mode=ref_mode).set(float(int(v)))
                # Phase C price compare metrics
                if self.metrics.price_usd is not None:
                    prices = getattr(dec, 'prices_by_source', {}) or {}
                    for src_name, val in prices.items():
                        try:
                            self.metrics.price_usd.labels(asset=asset, alternate_asset_name=alt_label, source=str(src_name)).set(float(val))
                        except Exception:
                            pass
                if self.metrics.price_delta_abs is not None:
                    v = getattr(dec, 'price_delta_abs', None)
                    if v is not None:
                        self.metrics.price_delta_abs.labels(asset=asset, alternate_asset_name=alt_label).set(float(v))
                if self.metrics.price_delta_rel is not None:
                    v = getattr(dec, 'price_delta_rel', None)
                    if v is not None:
                        self.metrics.price_delta_rel.labels(asset=asset, alternate_asset_name=alt_label).set(float(v))
                if self.metrics.price_mismatch is not None:
                    v = getattr(dec, 'price_mismatch', None)
                    if v is not None:
                        self.metrics.price_mismatch.labels(asset=asset, alternate_asset_name=alt_label).set(int(v))
                if self.metrics.price_compare_unavailable is not None:
                    v = getattr(dec, 'price_compare_unavailable', None)
                    if v is not None:
                        self.metrics.price_compare_unavailable.labels(asset=asset, alternate_asset_name=alt_label).set(int(v))
                # Rate metrics: best-effort latest sample per view's series, with standardized labels
                try:
                    from . import io_adapters as ioa
                    from .io_adapters import DataSourceName as DataSourceNameT
                    from typing import cast
                    cfg = self.settings.client
                    dr = getattr(cfg, 'date_range', None)
                    # Default provider: prefer first greptime-backed from normalized prices
                    try:
                        norm = getattr(self, '_norm', None)
                        prices = getattr(norm, 'prices', None) if norm is not None else None
                        prov = []
                        if prices is not None:
                            logicals = list(getattr(prices, 'logical_sources', []) or [])
                            prio = dict(getattr(prices, 'priority_by_logical', {}) or {})
                            seen = set()
                            keys = list(logicals) + [k for k in prio.keys() if k not in logicals]
                            for k in keys:
                                for p in (prio.get(k, []) or []):
                                    ps = str(p).strip()
                                    if ps.lower().startswith('greptime(') and ps not in seen:
                                        prov.append(ps); seen.add(ps)
                        default_source = prov[0] if prov else 'greptime(liqwid)'
                    except Exception:
                        default_source = 'greptime(liqwid)'
                    # USD rate
                    if self.metrics.rate_usd is not None:
                        s = cast(DataSourceNameT, default_source)
                        asset_sym = _resolved_symbol(asset)
                        series_usd = ioa.get_change_rate_series_usd(asset_sym, s, cfg, dr)
                        if series_usd and series_usd.series:
                            last_ts = max(series_usd.series.keys())
                            val = float(series_usd.series[last_ts])
                            self.metrics.rate_usd.labels(asset=asset, alternate_asset_name=alt_label, source=str(default_source)).set(val)
                    # ADA rate (minswap only)
                    if self.metrics.rate_ada is not None:
                        asset_sym = _resolved_symbol(asset)
                        series_ada = ioa.get_change_rate_series_ada(asset_sym, cfg, dr)
                        if series_ada and series_ada.series:
                            last_ts = max(series_ada.series.keys())
                            val = float(series_ada.series[last_ts])
                            # ADA rate comes from greptime(minswap)
                            self.metrics.rate_ada.labels(asset=asset, alternate_asset_name=alt_label, source='greptime(minswap)').set(val)
                except Exception:
                    pass

            try:
                payload = generate_latest(self.registry)
                with self._metrics_payload_lock:
                    self._metrics_payload = payload
            except Exception:
                pass
        except Exception:
            # Don't let metrics update crash the loop
            pass

    # ===== Dashboard helpers =====
    def _schedule_data_refresh(self, asset: str, requested_start: str, requested_end: str) -> None:
        """
        Background task to expand data.date_range and re-sync if plot range exceeds it.
        
        Strategy:
        1. Update expanded data range to encompass requested range
        2. Trigger transaction sync for new range
        3. Update cache for dashboard use
        
        Args:
            asset: Asset symbol
            requested_start: ISO datetime string for requested plot start
            requested_end: ISO datetime string for requested plot end
        """
        import threading
        from datetime import timezone
        
        def refresh_task():
            try:
                log = logging.getLogger(__name__)
                log.info(f"[PlotRange] Expanding data range for {asset}: {requested_start} to {requested_end}")
                
                # Parse requested times
                try:
                    req_start = datetime.fromisoformat(requested_start.replace('Z', '+00:00'))
                    req_end = datetime.fromisoformat(requested_end.replace('Z', '+00:00'))
                except Exception as e:
                    log.error(f"[PlotRange] Failed to parse datetime strings: {e}")
                    return
                
                # Expand current data range
                current_range = self.settings.client.date_range
                expanded_start = min(current_range.start, req_start) if current_range.start else req_start
                expanded_end_candidate = req_end
                if current_range.end:
                    expanded_end_candidate = max(current_range.end, req_end)
                else:
                    expanded_end_candidate = datetime.now(timezone.utc)
                
                expanded_range = DateRange(
                    start=expanded_start,
                    end=expanded_end_candidate
                )
                
                # Sync transactions for expanded range
                try:
                    syncer = TransactionSyncer(
                        settings=self.settings,
                        start_date=expanded_start.isoformat(),
                        end_date=expanded_end_candidate.isoformat()
                    )
                    syncer.sync_asset(asset)
                    log.info(f"[PlotRange] Transaction sync complete for {asset}")
                except Exception as e:
                    log.warning(f"[PlotRange] Transaction sync failed for {asset}: {e}")
                
                # Update internal state
                self._expanded_data_ranges[asset] = expanded_range
                
                log.info(f"[PlotRange] Data refresh complete for {asset}")
            except Exception as e:
                log.error(f"[PlotRange] Data refresh failed for {asset}: {e}")
        
        thread = threading.Thread(target=refresh_task, daemon=True, name=f"DataRefresh-{asset}")
        thread.start()
    
    def _get_assets_alpha(self) -> List[str]:
        # Prefer keys from latest decisions; fallback to configured assets
        assets = list(self.latest_decisions.keys()) or list(self.settings.client.assets or [])
        assets = [str(a) for a in assets]
        assets.sort(key=lambda s: s.lower())
        # Add "total" pseudo-asset for aggregated view
        assets.append("total")
        return assets

    def _select_asset(self, qs: Dict[str, List[str]]) -> str:
        assets = self._get_assets_alpha()
        if not assets:
            return ""
        cand = (qs.get("asset", [""])[0] or "").strip()
        if cand and cand.lower() in {a.lower() for a in assets}:
            # Return original-cased asset from list
            for a in assets:
                if a.lower() == cand.lower():
                    return a
        return assets[0]

    def _render_dashboard_html(self, asset: str, view: str, source: str, banner: Optional[str] = None, is_authenticated: bool = False) -> str:
        assets = self._get_assets_alpha()
        # Build dropdowns
        asset_opts = []
        for a in assets:
            sel = " selected" if a.lower() == asset.lower() else ""
            asset_opts.append(f"<option value=\"{a}\"{sel}>{a.upper()}</option>")
        view_opts = []
        view_text= ["Gains", "Adjusted transactions", "Raw transactions", "USD rate", "ADA rate"]
        view_list = ("gains_pct", "corrected", "raw", "rate_usd", "rate_ada")
        for i, v in enumerate(view_list):
            sel = " selected" if v == view else ""
            view_opts.append(f"<option value=\"{v}\"{sel}>{view_text[i]}</option>")
        # Prefer normalized provider list (greptime-backed) for the source dropdown
        try:
            norm = getattr(self, '_norm', None)
            prices = getattr(norm, 'prices', None) if norm is not None else None
            providers = []
            if prices is not None:
                logicals = list(getattr(prices, 'logical_sources', []) or [])
                prio = dict(getattr(prices, 'priority_by_logical', {}) or {})
                seen = set()
                keys = list(logicals) + [k for k in prio.keys() if k not in logicals]
                for k in keys:
                    for p in (prio.get(k, []) or []):
                        ps = str(p).strip()
                        if ps.lower().startswith('greptime(') and ps not in seen:
                            providers.append(ps); seen.add(ps)
            src_list = providers or ["greptime(liqwid)"]
        except Exception:
            src_list = ["greptime(liqwid)"]
        if source not in src_list:
            source = (src_list[0] if src_list else 'greptime(liqwid)')
        src_opts = []
        for s in src_list:
            sel = " selected" if s == source else ""
            src_opts.append(f"<option value=\"{s}\"{sel}>{s}</option>")
        
        # Build time unit dropdown options from config
        agg_config = self.settings.orchestrator.diagnostics.aggregation
        default_time_unit = agg_config.time_unit
        ui_time_units = agg_config.ui_time_units or ["5min", "15min", "1h", "6h", "1d", "3d", "1w"]
        
        # Map time units to friendly display names
        time_unit_labels = {
            "1min": "1 minute",
            "5min": "5 minutes",
            "15min": "15 minutes",
            "30min": "30 minutes",
            "1h": "1 hour",
            "6h": "6 hours",
            "12h": "12 hours",
            "1d": "1 day",
            "3d": "3 days",
            "1w": "1 week"
        }
        
        time_unit_opts = []
        for unit in ui_time_units:
            label = time_unit_labels.get(unit, unit)
            sel = " selected" if unit == default_time_unit else ""
            time_unit_opts.append(f"<option value=\"{unit}\"{sel}>{label}</option>")

        # Chart image
        img_b64, notice_msg, metrics = self._build_chart_b64(asset, view, source)
        img_src = f"data:image/png;base64,{img_b64}" if img_b64 else ""

        # Stats (privacy: keep simple; total has N/A fields)
        dec = self.latest_decisions.get(asset) if asset.lower() != "total" else None
        decision = getattr(dec, 'decision', 'N/A') if dec else 'N/A'
        ref_mode = getattr(dec, 'ref_mode', 'N/A') if dec else 'N/A'
        def _fmt(x: Optional[float], as_percent: bool = False, base: Optional[float] = None) -> str:
            if x is None:
                return "N/A"
            try:
                xf = float(x)
                if as_percent and base is not None and base != 0:
                    xf = (xf / base) * 100.0
            except Exception:
                return "N/A"
            return f"{xf:.2f}{'%' if as_percent else ''}"
        
        # Determine percentage base for gains_pct view (first valid data point)
        percent_base_value = None
        if view == 'gains_pct' and dec:
            # Try to get the first valid position value from v_ref_usd (reference position at t0)
            v_ref = getattr(dec, 'v_ref_usd', None)
            if v_ref is not None and float(v_ref) != 0:
                percent_base_value = float(v_ref)
        
        # Hide Wmax and r_now in public rate views (security: prevents disclosure of withdrawal limits)
        use_percent = (view == 'gains_pct') and (percent_base_value is not None)
        if view in ("rate_usd", "rate_ada"):
            wmax_value = None  # Will be excluded from stats bar
            residual_value = None
        elif dec and getattr(dec, 'wmax_usd', None):
            total_wmax = sum(wb.wmax_usd for wb in dec.wmax_usd)
            wmax_value = _fmt(total_wmax, as_percent=use_percent, base=percent_base_value)
        else:
            wmax_value = "N/A"
        residual_value = _fmt(getattr(dec, 'residual_usd', None), as_percent=use_percent, base=percent_base_value) if dec else "N/A"
        sigma_value = _fmt(getattr(dec, 'sigma_usd', None)) if dec else "N/A"
        k_sigma = _fmt(getattr(dec, 'k_sigma', None)) if dec else "N/A"
        residual_trigger = str(getattr(dec, 'residual_trigger', 'N/A')) if dec else 'N/A'

        banner_html = (
            f"<div style=\"background:#fee;border:1px solid #f99;padding:6px;margin-bottom:8px;color:#900;\">{banner}</div>"
            if banner else ""
        )
        notice_html = (
            f"<div style=\"background:#fee;border:1px dashed #f66;padding:6px;margin-bottom:8px;color:#a00;\">{notice_msg}</div>"
            if notice_msg else ""
        )

        # Get default sync dates from config for UI placeholders
        tx_sync = self.settings.orchestrator.transaction_sync
        default_start = tx_sync.start_date if tx_sync.start_date else "2023-10-01"
        default_end = tx_sync.end_date if tx_sync.end_date else "now"
        
        disable_src_attr = " disabled title=\"ADA rate uses Minswap (ada_usd)\"" if view == "rate_ada" else ""
        html = (
            "<!DOCTYPE html>\n"
            "<html><head>\n"
            "  <meta charset=\"utf-8\" />\n"
            "  <title>Alert Orchestrator Dashboard</title>\n"
            "  <style>\n"
            "    body { font-family: Arial, sans-serif; margin: 1rem; }\n"
            "    .controls { margin-bottom: 1rem; display: flex; gap: 1rem; align-items: center; flex-wrap: wrap; }\n"
            "    .controls input[type=\"date\"] { padding: 4px 8px; border: 1px solid #ccc; border-radius: 4px; font-size: 0.9em; }\n"
            "    .controls input[type=\"date\"]:not([value]):before { content: attr(placeholder); color: #999; }\n"
            "    .stats { margin: 0.5rem 0 1rem 0; }\n"
            "    .imgwrap { border: 1px solid #ddd; padding: 6px; display: inline-block; }\n"
            "    .hint { font-size: 0.85em; color: #666; font-style: italic; margin-left: 0.5rem; }\n"
            "  </style>\n"
            "  <script>\n"
            "    function nav() {\n"
            "      const a = document.getElementById('asset').value;\n"
            "      const v = document.getElementById('view').value;\n"
            "      const s = document.getElementById('source').value;\n"
            "      window.location.href = '/dashboard?asset=' + encodeURIComponent(a) + '&view=' + encodeURIComponent(v) + '&source=' + encodeURIComponent(s);\n"
            "    }\n"
            "    function refreshImage() { window.location.reload(); }\n"
            "    async function syncTransactions() {\n"
            "      const btn = document.getElementById('syncBtn');\n"
            "      const status = document.getElementById('syncStatus');\n"
            "      const startDate = document.getElementById('syncStartDate').value;\n"
            "      const endDate = document.getElementById('syncEndDate').value;\n"
            "      try {\n"
            "        btn.disabled = true;\n"
            "        status.innerHTML = '<span style=\"color: blue;\">⏳ Syncing...</span>';\n"
            "        \n"
            "        // Build request body with optional date range\n"
            "        const requestBody = {};\n"
            "        if (startDate) {\n"
            "          requestBody.start_date = startDate + 'T00:00:00Z';\n"
            "        }\n"
            "        if (endDate) {\n"
            "          requestBody.end_date = endDate + 'T23:59:59Z';\n"
            "        }\n"
            "        \n"
            "        const res = await fetch('/api/sync-transactions', {\n"
            "          method: 'POST',\n"
            "          headers: { 'Content-Type': 'application/json' },\n"
            "          body: JSON.stringify(requestBody)\n"
            "        });\n"
            "        const data = await res.json();\n"
            "        if (data.success) {\n"
            "          status.innerHTML = '<span style=\"color: green;\">✓ Synced: ' + data.new_transactions + ' new, ' + data.duplicates_skipped + ' skipped</span>';\n"
            "          setTimeout(() => { status.innerHTML = ''; }, 5000);\n"
            "        } else {\n"
            "          status.innerHTML = '<span style=\"color: red;\">✗ Error: ' + (data.error || 'Unknown error') + '</span>';\n"
            "        }\n"
            "      } catch (e) {\n"
            "        status.innerHTML = '<span style=\"color: red;\">✗ Network error: ' + e.message + '</span>';\n"
            "      } finally {\n"
            "        btn.disabled = false;\n"
            "      }\n"
            "    }\n"
            "  </script>\n"
            "</head>\n"
            "<body>\n"
            f"{banner_html}{notice_html}\n"
            "  <div class=\"controls\">\n"
            "    <label>Asset: <select id=\"asset\" onchange=\"nav()\">" + "".join(asset_opts) + "</select></label>\n"
            "    <label>View: <select id=\"view\" onchange=\"nav()\">" + "".join(view_opts) + "</select></label>\n"
            f"    <label>Source: <select id=\"source\" onchange=\"nav()\"{disable_src_attr}>" + "".join(src_opts) + "</select></label>\n"
            "    <button onclick=\"refreshImage()\">Refresh</button>\n"
            "    <span style=\"margin-left: 1rem; border-left: 1px solid #ccc; padding-left: 1rem;\"></span>\n"
        )
        
        # Sync controls: disable if not authenticated
        if is_authenticated:
            sync_disabled_attr = ""
            sync_button_style = " style=\"background-color: #28a745; color: white; border: 1px solid #1e7e34; padding: 6px 12px; border-radius: 4px; cursor: pointer;\""
            sync_title = ""
        else:
            sync_disabled_attr = " disabled style=\"opacity: 0.5; cursor: not-allowed;\""
            sync_button_style = " style=\"background-color: #dc3545; color: white; border: 1px solid #c82333; padding: 6px 12px; border-radius: 4px; cursor: not-allowed;\" disabled"
            sync_title = " title=\"Login required (access corrected/raw view first)\""
        
        # Transaction sync section will be rendered after aggregation controls (below)
        
        html += (
            "  </div>\n"
            "  <div class=\"stats\">\n"
        )
        # Build stats bar, conditionally including Wmax
        wmax_html = f"<strong>Wmax:</strong> {wmax_value} &nbsp; | &nbsp; " if wmax_value is not None else ""
        html += (
            f"    <strong>Decision:</strong> {decision} &nbsp; | &nbsp; <strong>Ref:</strong> {ref_mode} &nbsp; | &nbsp; "
            f"{wmax_html}<strong>r_now:</strong> {residual_value} &nbsp; | &nbsp; "
            f"<strong>σ:</strong> {sigma_value} &nbsp; | &nbsp; <strong>k:</strong> {k_sigma} &nbsp; | &nbsp; <strong>triggered:</strong> {residual_trigger}\n"
            "  </div>\n"
        )

        # Wallet breakdown list (only if authenticated and not rate view)
        if is_authenticated and view not in ("rate_usd", "rate_ada") and dec and getattr(dec, 'wmax_usd', None):
            w_items = []
            for wb in dec.wmax_usd:
                val_str = _fmt(wb.wmax_usd, as_percent=use_percent, base=percent_base_value)
                v_str = _fmt(getattr(wb, 'v_t1_usd', None), as_percent=False)
                addr = wb.abbreviated_address()
                w_items.append(f"<li>{addr}: {val_str} (v={v_str})</li>")
            if w_items:
                html += f"<div style='margin-bottom: 1rem; font-size: 0.9em;'><strong>Wallet Breakdown:</strong><ul style='margin-top: 4px; margin-bottom: 0;'>{''.join(w_items)}</ul></div>\n"

        html += (
            "  <div class=\"imgwrap\">\n"
            f"    <img id=\"diag\" src=\"{img_src}\" alt=\"dashboard plot\" style=\"max-width: 95vw; height: auto;\" />\n"
            "  </div>\n"
        )

        # Model Gains Summary Panel
        if metrics:
            unit = "%" if use_percent else "USD"
            interval = metrics.get("interval_days", 0.0)
            g_month = metrics.get("model_gains_per_month")
            g_year = metrics.get("model_gains_per_year")
            
            # Only show if gains are calculated (not None)
            if g_month is not None and g_year is not None:
                html += (
                    f"  <div style=\"margin-top: 1rem; padding: 10px; background: #f8f9fa; border: 1px solid #ddd; display: inline-block;\">\n"
                    f"    <strong>Model Gains (Projected):</strong><br/>\n"
                    f"    <span style=\"font-size: 0.9em; color: #555;\">Period: {interval:.2f} days</span><br/>\n"
                    f"    <ul style=\"margin: 4px 0 0 1.2rem; padding: 0;\">\n"
                    f"      <li>{g_month:.2f} {unit} / month</li>\n"
                    f"      <li>{g_year:.2f} {unit} / year</li>\n"
                    f"    </ul>\n"
                    f"  </div>\n"
                )
            else:
                html += (
                    f"  <div style=\"margin-top: 1rem; padding: 10px; background: #f8f9fa; border: 1px solid #ddd; display: inline-block;\">\n"
                    f"    <strong>Model Gains (Projected):</strong><br/>\n"
                    f"    <span style=\"font-size: 0.9em; color: #555;\">Period: {interval:.2f} days</span><br/>\n"
                    f"    <span style=\"color: #777;\">N/A (Trend method not supported for projection)</span>\n"
                    f"  </div>\n"
                )

        # Plot Range Control Panel (Phase C)
        # Get current date range for initialization
        dr = self.settings.client.date_range
        start_val = dr.start.strftime('%Y-%m-%dT%H:%M') if dr.start else ""
        end_val = dr.end.strftime('%Y-%m-%dT%H:%M') if dr.end else ""
        
        html += (
            f"  <div style=\"margin-top: 1rem; padding: 10px; background: #e8f4f8; border: 1px solid #0288d1;\">\n"
            f"    <strong style=\"color: #01579b;\">📊 Plot Range Control</strong><br/>\n"
            f"    <span style=\"font-size: 0.85em; color: #555;\">Adjust the visible time window without re-syncing data</span><br/><br/>\n"
            f"    <label style=\"display: inline-block; margin-right: 1rem;\">\n"
            f"      <strong>Start:</strong> <input type=\"datetime-local\" id=\"plotStart\" value=\"{start_val}\" style=\"padding: 4px; border: 1px solid #ccc; border-radius: 3px;\" />\n"
            f"    </label>\n"
            f"    <label style=\"display: inline-block; margin-right: 1rem;\">\n"
            f"      <strong>End:</strong> <input type=\"datetime-local\" id=\"plotEnd\" value=\"{end_val}\" style=\"padding: 4px; border: 1px solid #ccc; border-radius: 3px;\" />\n"
            f"    </label>\n"
            f"    <button onclick=\"updatePlotRange()\" style=\"padding: 6px 12px; background: #0288d1; color: white; border: none; border-radius: 3px; cursor: pointer; margin-right: 0.5rem;\">Update Range</button>\n"
            f"    <button onclick=\"resetPlotRange()\" style=\"padding: 6px 12px; background: #757575; color: white; border: none; border-radius: 3px; cursor: pointer;\">Reset to Config</button>\n"
            f"    <span id=\"plotRangeStatus\" style=\"margin-left: 1rem; font-size: 0.9em; color: #666;\"></span>\n"
            f"    <br/><br/>\n"
            f"    <strong style=\"color: #01579b;\">📈 Gains Reference Point:</strong><br/>\n"
            f"    <span style=\"font-size: 0.85em; color: #555;\">Choose the starting point for gains calculation</span><br/>\n"
            f"    <label style=\"margin-right: 1.5rem; cursor: pointer;\">\n"
            f"      <input type=\"radio\" id=\"refAlert\" name=\"gainsRef\" value=\"alert_driven\" checked style=\"margin-right: 4px;\" />\n"
            f"      <span>Last Alert-Driven Transaction</span>\n"
            f"    </label>\n"
            f"    <label style=\"cursor: pointer;\">\n"
            f"      <input type=\"radio\" id=\"refPlot\" name=\"gainsRef\" value=\"plot_range\" style=\"margin-right: 4px;\" />\n"
            f"      <span>Start of Plot Range</span>\n"
            f"    </label>\n"
            f"  </div>\n"
            f"\n"
            f"  <div style=\"margin-top: 1rem; padding: 10px; background: #f3e5f5; border: 1px solid #9c27b0;\">\n"
            f"    <strong style=\"color: #6a1b9a;\">📉 Data Aggregation</strong><br/>\n"
            f"    <span style=\"font-size: 0.85em; color: #555;\">Aggregate dense data for better performance with whisker plots</span><br/><br/>\n"
            f"    <label style=\"display: inline-block; margin-right: 1.5rem; cursor: pointer;\">\n"
            f"      <input type=\"checkbox\" id=\"aggEnabled\" {'checked' if agg_config.enabled else ''} style=\"margin-right: 4px;\" />\n"
            f"      <span>Enable Aggregation</span>\n"
            f"    </label>\n"
            f"    <label style=\"display: inline-block; margin-right: 1.5rem;\">\n"
            f"      <strong>Time Unit:</strong>\n"
            f"      <select id=\"aggTimeUnit\" style=\"padding: 4px; border: 1px solid #ccc; border-radius: 3px; margin-left: 4px;\">\n"
            f"        {''.join(time_unit_opts)}\n"
            f"      </select>\n"
            f"    </label>\n"
            f"    <label style=\"display: inline-block; margin-right: 1.5rem; cursor: pointer;\">\n"
            f"      <input type=\"checkbox\" id=\"aggShowRaw\" {'checked' if agg_config.show_raw_points else ''} style=\"margin-right: 4px;\" />\n"
            f"      <span>Show Raw Points</span>\n"
            f"    </label>\n"
            f"    <button onclick=\"updateAggregation()\" style=\"padding: 6px 12px; background: #9c27b0; color: white; border: none; border-radius: 3px; cursor: pointer; margin-right: 0.5rem;\">Apply Aggregation</button>\n"
            f"    <span id=\"aggStatus\" style=\"margin-left: 1rem; font-size: 0.9em; color: #666;\"></span>\n"
            f"  </div>\n"
            f"\n"
            f"  <div style=\"margin-top: 1rem; padding: 10px; background: #fff3e0; border: 1px solid #ff9800;\">\n"
            f"    <strong style=\"color: #e65100;\">🔄 Transaction Sync</strong><br/>\n"
            f"    <span style=\"font-size: 0.85em; color: #555;\">Synchronize transaction data for updated position calculations</span><br/><br/>\n"
            f"    <label style=\"display: inline-block; margin-right: 1rem;\">\n"
            f"      <strong>Sync From:</strong> <input type=\"date\" id=\"syncStartDate\" placeholder=\"{default_start}\" title=\"Default: {default_start}\"{sync_disabled_attr} style=\"padding: 4px; border: 1px solid #ccc; border-radius: 3px;\" />\n"
            f"    </label>\n"
            f"    <label style=\"display: inline-block; margin-right: 1rem;\">\n"
            f"      <strong>To:</strong> <input type=\"date\" id=\"syncEndDate\" placeholder=\"{default_end}\" title=\"Default: {default_end}\"{sync_disabled_attr} style=\"padding: 4px; border: 1px solid #ccc; border-radius: 3px;\" />\n"
            f"    </label>\n"
            f"    <button id=\"syncBtn\" onclick=\"syncTransactions()\"{sync_button_style}{sync_title}>Sync Transactions</button>\n"
            f"    <span id=\"syncStatus\" style=\"margin-left: 1rem; font-size: 0.9em; color: #666;\"></span><br/>\n"
            f"    <span style=\"font-size: 0.8em; color: #777;\">(empty = defaults: {default_start} to {default_end})</span>\n"
            f"  </div>\n"
        )

        # JavaScript for plot range and aggregation controls
        html += """
  <script>
    function getCurrentAsset() {
      const urlParams = new URLSearchParams(window.location.search);
      return urlParams.get('asset') || 'usdc';
    }
    
    async function updatePlotRange() {
      const start = document.getElementById('plotStart').value;
      let end = document.getElementById('plotEnd').value;
      const ref = document.querySelector('input[name="gainsRef"]:checked').value;
      const statusEl = document.getElementById('plotRangeStatus');
      
      // Validation
      if (!start) {
        statusEl.textContent = '⚠️ Please select a start time';
        statusEl.style.color = '#d32f2f';
        return;
      }
      
      // If no end time provided, default to current time
      if (!end) {
        const now = new Date();
        end = now.toISOString().slice(0, 16);  // Format: YYYY-MM-DDTHH:MM
        document.getElementById('plotEnd').value = end;
      }
      
      if (new Date(start) >= new Date(end)) {
        statusEl.textContent = '⚠️ Start time must be before end time';
        statusEl.style.color = '#d32f2f';
        return;
      }
      
      statusEl.textContent = '⏳ Updating...';
      statusEl.style.color = '#ff9800';
      
      try {
        const res = await fetch('/api/update-plot-range', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            asset: getCurrentAsset(),
            start: start + ':00Z',  // Add seconds and UTC marker
            end: end + ':00Z',
            gains_reference: ref
          })
        });
        
        const data = await res.json();
        
        if (res.ok && data.success) {
          if (data.data_refresh_triggered) {
            statusEl.textContent = '✓ Range updated. Background data sync in progress (~' + data.eta_seconds + 's)';
            statusEl.style.color = '#ff9800';
            // Reload after estimated sync time
            setTimeout(() => window.location.reload(), (data.eta_seconds + 2) * 1000);
          } else {
            statusEl.textContent = '✓ Range updated successfully';
            statusEl.style.color = '#388e3c';
            // Reload to show new plot
            setTimeout(() => window.location.reload(), 1000);
          }
        } else {
          statusEl.textContent = '❌ Error: ' + (data.error || 'Unknown error');
          statusEl.style.color = '#d32f2f';
        }
      } catch (error) {
        statusEl.textContent = '❌ Network error: ' + error.message;
        statusEl.style.color = '#d32f2f';
      }
    }
    
    function resetPlotRange() {
      const statusEl = document.getElementById('plotRangeStatus');
      statusEl.textContent = '⏳ Resetting to config defaults...';
      statusEl.style.color = '#ff9800';
      
      // Clear the datetime inputs to signal reset
      document.getElementById('plotStart').value = '';
      document.getElementById('plotEnd').value = '';
      
      // Reset gains reference to default
      document.getElementById('refAlert').checked = true;
      
      statusEl.textContent = '✓ Reset to config. Reloading...';
      statusEl.style.color = '#388e3c';
      
      // Reload page to use config defaults
      setTimeout(() => window.location.reload(), 500);
    }
    
    async function updateAggregation() {
      const enabled = document.getElementById('aggEnabled').checked;
      const timeUnit = document.getElementById('aggTimeUnit').value;
      const showRaw = document.getElementById('aggShowRaw').checked;
      const asset = getCurrentAsset();
      const statusEl = document.getElementById('aggStatus');
      
      statusEl.textContent = '⏳ Updating aggregation settings...';
      statusEl.style.color = '#ff9800';
      
      try {
        const res = await fetch('/api/update-aggregation', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            asset: asset,
            enabled: enabled,
            time_unit: timeUnit,
            show_raw_points: showRaw
          })
        });
        
        const data = await res.json();
        
        if (data.success) {
          const mode = enabled ? `enabled (${timeUnit})` : 'disabled';
          statusEl.textContent = `✓ Aggregation ${mode}. Reloading...`;
          statusEl.style.color = '#388e3c';
          
          // Reload page to apply aggregation
          setTimeout(() => window.location.reload(), 500);
        } else {
          statusEl.textContent = `⚠️ Error: ${data.error || 'Unknown error'}`;
          statusEl.style.color = '#d32f2f';
        }
      } catch (err) {
        statusEl.textContent = `⚠️ Network error: ${err.message}`;
        statusEl.style.color = '#d32f2f';
      }
    }
    
    // Show current plot range on load
    window.addEventListener('DOMContentLoaded', () => {
      const start = document.getElementById('plotStart').value;
      const end = document.getElementById('plotEnd').value;
      const statusEl = document.getElementById('plotRangeStatus');
      
      if (start && end) {
        const startDate = new Date(start);
        const endDate = new Date(end);
        const days = Math.round((endDate - startDate) / (1000 * 60 * 60 * 24));
        statusEl.textContent = `Current range: ${days} days`;
        statusEl.style.color = '#0288d1';
      }
    });
  </script>
"""

        html += "</body></html>\n"
        return html

    def _is_view_allowed(self, headers, view: str) -> bool:
        # gains_pct is always allowed
        if view == "gains_pct":
            return True
        # If auth disabled, disallow private views by default
        cfg = self.settings.orchestrator.auth
        if not cfg or not getattr(cfg, 'enabled', False):
            return False
        # Basic Auth check
        auth_header = headers.get('Authorization') if headers else None
        if not auth_header or not auth_header.startswith('Basic '):
            return False
        try:
            b64 = auth_header.split(' ', 1)[1]
            raw = base64.b64decode(b64).decode('utf-8')
            user, pwd = raw.split(':', 1)
        except Exception:
            return False
        import os
        u_env = os.getenv(cfg.user_env or 'WO_BASIC_AUTH_USER')
        p_env = os.getenv(cfg.pass_env or 'WO_BASIC_AUTH_PASS')
        return bool(u_env) and bool(p_env) and (user == u_env) and (pwd == p_env)

    def _is_authenticated(self, headers) -> bool:
        """Check if request has valid auth credentials (without sending 401).
        Returns True if authenticated, False otherwise.
        """
        cfg = getattr(self.settings.orchestrator, 'auth', None)
        if not cfg or not getattr(cfg, 'enabled', False):
            return False  # Auth not configured = not authenticated
        
        auth_header = headers.get('Authorization') if headers else None
        if not auth_header or not auth_header.startswith('Basic '):
            return False
        
        try:
            import os
            import hmac
            b64 = auth_header.split(' ', 1)[1]
            raw = base64.b64decode(b64).decode('utf-8')
            user, pwd = raw.split(':', 1)
            
            user_env = getattr(cfg, 'user_env', 'WO_BASIC_AUTH_USER')
            pass_env = getattr(cfg, 'pass_env', 'WO_BASIC_AUTH_PASS')
            expected_user = os.getenv(user_env) or ""
            expected_pass = os.getenv(pass_env) or ""
            
            return (expected_user and expected_pass and 
                    hmac.compare_digest(user, expected_user) and 
                    hmac.compare_digest(pwd, expected_pass))
        except Exception:
            return False

    def _check_basic_auth(self, handler: BaseHTTPRequestHandler) -> bool:
        """Validate Basic Auth using env vars; on failure, send 401 with WWW-Authenticate.
        Returns True if authorized, False if denied (response already sent).
        """
        cfg = getattr(self.settings.orchestrator, 'auth', None)
        # If auth is not configured/enabled, deny private access with a challenge
        user_env = getattr(cfg, 'user_env', 'WO_BASIC_AUTH_USER') if cfg else 'WO_BASIC_AUTH_USER'
        pass_env = getattr(cfg, 'pass_env', 'WO_BASIC_AUTH_PASS') if cfg else 'WO_BASIC_AUTH_PASS'
        import os
        expected_user = os.getenv(user_env) or ""
        expected_pass = os.getenv(pass_env) or ""
        auth_header = handler.headers.get('Authorization') if handler and handler.headers else None
        # Helper to send 401 challenge
        def challenge():
            try:
                handler.send_response(401)
                handler.send_header('WWW-Authenticate', 'Basic realm="WO Dashboard", charset="UTF-8"')
                handler.end_headers()
            except Exception:
                pass
        # Require enabled config and non-empty expected creds
        if not (cfg and getattr(cfg, 'enabled', False) and expected_user and expected_pass):
            challenge()
            return False
        if not auth_header or not auth_header.startswith('Basic '):
            challenge()
            return False
        try:
            b64 = auth_header.split(' ', 1)[1]
            raw = base64.b64decode(b64).decode('utf-8')
            user, pwd = raw.split(':', 1)
        except Exception:
            challenge()
            return False
        ok = hmac.compare_digest(user, expected_user) and hmac.compare_digest(pwd, expected_pass)
        if not ok:
            challenge()
            return False
        return True

    def _build_total_chart_b64(self, view: str, source: str) -> tuple[Optional[str], Optional[str], Optional[Dict[str, float]]]:
        """Aggregate all assets onto common timebase and plot. Returns (img_b64, notice_or_error, metrics)."""
        try:
            import numpy as np
            from datetime import timedelta, datetime, timezone
            import base64
            import logging
        except Exception as e:
            return None, str(e), None

        cfg = self.settings.client
        assets = list(cfg.assets or [])
        if not assets:
            return None, "No assets configured", None

        reader = GreptimeReader(cfg.greptime, cfg.table_asset_prefix)
        resolver = Resolver(greptime_reader=reader)
        dr = getattr(cfg, 'date_range', None)

        resolved_assets: Dict[str, str] = {}
        for a in assets:
            try:
                _mid, sym = resolver.resolve_asset(a)
                resolved_assets[a] = sym if sym else a
            except Exception:
                resolved_assets[a] = a
        if not resolved_assets:
            return None, "No assets resolved", None

        from . import io_adapters as ioa
        from .io_adapters import DataSourceName as DataSourceNameT
        from typing import cast, List, Dict, Tuple
        # Prefer normalized duty threshold when available
        norm = getattr(self, '_norm', None)
        prices_norm = getattr(norm, 'prices', None) if norm is not None else None
        if prices_norm is not None:
            duty_thr = float(getattr(prices_norm, 'duty_cycle_threshold', 0.9))
        else:
            try:
                duty_thr = float(getattr(getattr(getattr(self.settings.orchestrator, 'prices_v2', None), 'duty_cycle_threshold', 0.9), '__float__', lambda: 0.9)())
            except Exception:
                duty_thr = 0.9
        selected_source = source
        notices: List[str] = []
        log = logging.getLogger(__name__)

        def build_usd_position_series(asset_sym: str):
            units_series = reader.fetch_asset_units_series(asset_sym, dr)
            price_series = ioa.get_price_series(asset_sym, cast(DataSourceNameT, selected_source), cfg, dr)
            dc = ioa.compute_duty_cycle(price_series, dr)
            if dc < duty_thr:
                if selected_source != "greptime(liqwid)":
                    fb = "greptime(liqwid)"
                    price_series_fb = ioa.get_price_series(asset_sym, cast(DataSourceNameT, fb), cfg, dr)
                    if price_series_fb and price_series_fb.series:
                        notices.append(f"Price source '{selected_source}' had low duty cycle ({dc:.0%}); fell back to Greptime (liqwid).")
                        log.warning(f"Duty cycle {dc:.3f} below threshold for {asset_sym} using {selected_source}; falling back to greptime(liqwid)")
                        price_series_local = price_series_fb
                    else:
                        return None, None, f"Selected and fallback price sources unavailable for {asset_sym}"
                else:
                    notices.append(f"Price source '{selected_source}' had low duty cycle ({dc:.0%}).")
                    price_series_local = price_series
            else:
                price_series_local = price_series
            if not (units_series and units_series.series):
                return None, None, f"No units series for {asset_sym}"
            if not (price_series_local and price_series_local.series):
                return None, None, f"No price series for {asset_sym}"
            ts_units = sorted(units_series.series.keys())
            ts_price = sorted(price_series_local.series.keys())
            tb_union_all = sorted(set(ts_units) | set(ts_price))
            first_units_ts = ts_units[0].astimezone(timezone.utc) if ts_units[0].tzinfo else ts_units[0].replace(tzinfo=timezone.utc)
            tb_union = [t for t in tb_union_all if (t.astimezone(timezone.utc) if t.tzinfo else t.replace(tzinfo=timezone.utc)) >= first_units_ts]
            units_vals = [float(units_series.series[t]) for t in ts_units]
            price_vals = [float(price_series_local.series[t]) for t in ts_price]
            units_interp = interpolate_positions_on_timebase(ts_units, units_vals, np.array(tb_union), 'linear')
            price_interp = interpolate_positions_on_timebase(ts_price, price_vals, np.array(tb_union), 'linear')
            usd_positions = np.array(units_interp) * np.array(price_interp)
            return tb_union, usd_positions, None

        if view == 'raw':
            per_asset_series: List[Tuple[List[datetime], np.ndarray]] = []
            master_ts_set: set = set()
            errors: List[str] = []
            for _disp, sym in resolved_assets.items():
                tb_union, usd_positions, err = build_usd_position_series(sym)
                if err:
                    errors.append(err)
                    continue
                if tb_union is None or usd_positions is None:
                    continue
                per_asset_series.append((tb_union, usd_positions))
                master_ts_set.update(tb_union)
            if not per_asset_series:
                return None, ('; '.join(errors) if errors else 'No data'), None
            master_tb = sorted(master_ts_set)
            aggregated_positions = np.zeros(len(master_tb), dtype=float)
            for tb_union, usd_positions in per_asset_series:
                interp = interpolate_positions_on_timebase(tb_union, list(usd_positions), np.array(master_tb), 'linear')
                if tb_union:
                    first_ts = tb_union[0]
                    interp = np.array([
                        0.0 if t < first_ts else float(v)
                        for t, v in zip(master_tb, interp)
                    ], dtype=float)
                aggregated_positions += np.array(interp)
            x_time_use = np.array(master_tb)
            cp_vals_use = aggregated_positions
        else:
            all_position_timestamps: List[datetime] = []
            all_transaction_timestamps: List[datetime] = []
            asset_data: Dict[str, Tuple[List[datetime], np.ndarray, List]] = {}
            for display_asset, resolved in resolved_assets.items():
                try:
                    tb_union, usd_positions, err = build_usd_position_series(resolved)
                    if err or not tb_union:
                        continue
                    txs = reader.fetch_transactions(
                        asset_symbol=resolved,
                        deposits_prefix=cfg.deposits_prefix,
                        withdrawals_prefix=cfg.withdrawals_prefix,
                        date_range=dr,
                    )
                    if usd_positions is None:
                        continue
                    asset_data[display_asset] = (tb_union, usd_positions, txs)
                    all_position_timestamps.extend(tb_union)
                    all_transaction_timestamps.extend([tx.timestamp for tx in txs])
                except Exception:
                    continue
            if not asset_data:
                return None, "No data", None
            if len(asset_data) == 1:
                (display_asset, (pos_ts_single, pos_vals_single, txs_single)) = next(iter(asset_data.items()))
                wallets_series = reader.fetch_asset_series_by_wallet(resolved_assets[display_asset], dr) or {}
                all_wallet_pos_ts: List[datetime] = []
                all_wallet_tx_ts: List[datetime] = []
                wallet_txs_map: Dict[str, List] = {}
                use_created = str(getattr(self.settings.client, 'tx_timestamp_source', 'timestamp')).lower() == 'created_at'
                for wallet_addr, series in wallets_series.items():
                    w_ts = sorted(series.series.keys())
                    all_wallet_pos_ts.extend(w_ts)
                    try:
                        w_txs = reader.fetch_transactions(
                            asset_symbol=resolved_assets[display_asset],
                            deposits_prefix=cfg.deposits_prefix,
                            withdrawals_prefix=cfg.withdrawals_prefix,
                            date_range=dr,
                            wallet_address=wallet_addr,
                        )
                    except Exception:
                        w_txs = []
                    wallet_txs_map[wallet_addr] = w_txs
                    all_wallet_tx_ts.extend([tx.timestamp for tx in w_txs])
                if not all_wallet_pos_ts:
                    unified_timebase = create_unified_timebase(
                        position_timestamps=all_position_timestamps,
                        transaction_timestamps=all_transaction_timestamps
                    )
                    aggregated_positions = np.zeros(len(unified_timebase), dtype=float)
                    all_transactions: List = []
                    for _disp, (pt, pv, txs) in asset_data.items():
                        aggregated_positions += interpolate_positions_on_timebase(
                            position_timestamps=pt,
                            position_values=list(pv),
                            unified_timebase=unified_timebase,
                            interpolation_method='linear'
                        )
                        all_transactions.extend(txs)
                    tb, interp_pos, _d_cdf, _w_cdf, gains = calculate_correct_gains(
                        position_timestamps=list(unified_timebase),
                        position_values=list(aggregated_positions),
                        transactions=all_transactions,
                        reference_time_index=0,
                        interpolation_method='linear',
                        alignment_method=str(self.settings.client.alignment_method),
                        tx_timestamp_source=str(getattr(self.settings.client, 'tx_timestamp_source', 'timestamp')),
                    )
                    P_t0 = float(interp_pos[0]) if len(interp_pos) > 0 else 0.0
                    corrected = np.array(gains) + P_t0
                    x_time_use = np.array(unified_timebase)
                    cp_vals_use = corrected
                else:
                    master_tb = create_unified_timebase(
                        position_timestamps=all_wallet_pos_ts,
                        transaction_timestamps=all_wallet_tx_ts,
                    )
                    corrected_sum = np.zeros(len(master_tb), dtype=float)
                    from datetime import timezone as _tz
                    align_method = str(self.settings.client.alignment_method)
                    for wallet_addr, series in wallets_series.items():
                        w_ts = sorted(series.series.keys())
                        w_vals = [float(series.series[t]) for t in w_ts]
                        interp_w = interpolate_positions_on_timebase(
                            position_timestamps=w_ts,
                            position_values=w_vals,
                            unified_timebase=master_tb,
                            interpolation_method='linear'
                        )
                        pos_ts_set = set([(t if t.tzinfo else t.replace(tzinfo=_tz.utc)) for t in w_ts])
                        d_vec, w_vec = create_transaction_vectors_on_timebase(
                            wallet_txs_map.get(wallet_addr, []),
                            master_tb,
                            interpolated_positions=interp_w,
                            position_timestamps_set=pos_ts_set,
                            alignment_method=align_method,
                        )
                        d_cdf = np.cumsum(d_vec)
                        w_cdf = np.cumsum(w_vec)
                        P_t0 = float(interp_w[0]) if len(interp_w) else 0.0
                        D0 = float(d_cdf[0]) if len(d_cdf) else 0.0
                        W0 = float(w_cdf[0]) if len(w_cdf) else 0.0
                        gains_w = (interp_w - P_t0) - (d_cdf - D0) + (w_cdf - W0)
                        corrected_w = gains_w + P_t0
                        corrected_sum += corrected_w
                    x_time_use = np.array(master_tb)
                    cp_vals_use = corrected_sum
            else:
                master_tb = create_unified_timebase(
                    position_timestamps=all_position_timestamps,
                    transaction_timestamps=all_transaction_timestamps
                )
                corrected_sum = np.zeros(len(master_tb), dtype=float)
                align_method = str(self.settings.client.alignment_method)
                use_created = str(getattr(self.settings.client, 'tx_timestamp_source', 'timestamp')).lower() == 'created_at'
                from datetime import timezone as _tz
                for _disp, (pt, pv, txs) in asset_data.items():
                    interp = interpolate_positions_on_timebase(
                        position_timestamps=pt,
                        position_values=list(pv),
                        unified_timebase=master_tb,
                        interpolation_method='linear'
                    )
                    if pt:
                        first_ts = pt[0]
                        interp = np.array([
                            0.0 if t < first_ts else float(v)
                            for t, v in zip(master_tb, interp)
                        ], dtype=float)
                    pos_ts_set = set([(t if t.tzinfo else t.replace(tzinfo=_tz.utc)) for t in pt])
                    _txs_in = txs
                    if use_created:
                        try:
                            from dataclasses import replace as _replace
                            _txs_in = [_replace(tx, timestamp=getattr(tx, 'created_at', tx.timestamp)) for tx in txs]
                        except Exception:
                            pass
                    d_vec, w_vec = create_transaction_vectors_on_timebase(
                        _txs_in, master_tb,
                        interpolated_positions=interp,
                        position_timestamps_set=pos_ts_set,
                        alignment_method=align_method,
                    )
                    d_cdf = np.cumsum(d_vec)
                    w_cdf = np.cumsum(w_vec)
                    P_t0 = float(interp[0]) if len(interp) else 0.0
                    D0 = float(d_cdf[0]) if len(d_cdf) else 0.0
                    W0 = float(w_cdf[0]) if len(w_cdf) else 0.0
                    gains_i = (interp - P_t0) - (d_cdf - D0) + (w_cdf - W0)
                    corrected_sum += (gains_i + P_t0)
                x_time_use = np.array(master_tb)
                cp_vals_use = corrected_sum

        deposit_timestamps_list: List[datetime] = []
        withdrawal_timestamps_list: List[datetime] = []
        for _disp, resolved in resolved_assets.items():
            try:
                txs = reader.fetch_transactions(
                    asset_symbol=resolved,
                    deposits_prefix=cfg.deposits_prefix,
                    withdrawals_prefix=cfg.withdrawals_prefix,
                    date_range=dr,
                )
                deposit_timestamps_list.extend([tx.timestamp for tx in txs if tx.transaction_type == 'deposit'])
                withdrawal_timestamps_list.extend([tx.timestamp for tx in txs if tx.transaction_type == 'withdrawal'])
            except Exception:
                continue

        dg = self.settings.orchestrator.decision_gate
        dbg = self.settings.orchestrator.diagnostics
        eff_lookback = dbg.lookback_hours_override if getattr(dbg, 'lookback_hours_override', None) is not None else getattr(dg, 'lookback_hours', None)
        if eff_lookback is not None and len(x_time_use) > 0:
            try:
                t_cut = x_time_use[-1] - timedelta(hours=float(eff_lookback))
                mask_lkb = x_time_use >= t_cut
                if mask_lkb.sum() >= max(int(dg.min_points), int(dg.polynomial_order) + 1):
                    x_time_use = x_time_use[mask_lkb]
                    cp_vals_use = cp_vals_use[mask_lkb]
            except Exception:
                pass

        poly_order = 2
        if len(cp_vals_use) >= max(int(dg.min_points), poly_order + 1):
            t0_base = x_time_use[0]
            x_hours = np.array([(ts - t0_base).total_seconds() / 3600.0 for ts in x_time_use], dtype=float)
            coeffs = np.polyfit(x_hours, cp_vals_use, poly_order)
            poly = np.poly1d(coeffs)
            fitted = poly(x_hours)
        else:
            import numpy as _np
            fitted = _np.full_like(cp_vals_use, float(_np.mean(cp_vals_use)) if len(cp_vals_use) else 0.0, dtype=float)

        residuals = cp_vals_use - fitted
        try:
            if bool(getattr(dg, 'exclude_last_for_sigma', True)) and len(residuals) > 1:
                res_est = residuals[:-1]
            else:
                res_est = residuals
        except Exception:
            res_est = residuals
        import numpy as _np2
        sigma = float(_np2.std(res_est, ddof=0)) if len(res_est) > 0 else 0.0
        thr_low = None
        thr_high = None
        if str(getattr(dg, 'threshold_mode', 'stddev')).lower() == 'percentile' and len(res_est) > 1:
            try:
                c = float(getattr(dg, 'central_confidence', 0.68))
                p_low = max(0.0, (1.0 - c) / 2.0)
                p_high = min(1.0, 1.0 - p_low)
                q = _np2.quantile(res_est, [p_low, p_high])
                thr_low = float(q[0])
                thr_high = float(q[1])
            except Exception:
                thr_low = None
                thr_high = None
        r_now = float(residuals[-1]) if len(residuals) else 0.0
        k = float(getattr(dg, 'k_sigma', 2.0))
        
        model_gains_metrics = {}
        yaxis_mode_val = 'percent' if view == 'gains_pct' else 'absolute'
        
        # Phase E: Aggregation support for "total" asset
        aggregated_data = None
        show_raw_points = True
        agg_override = self._aggregation_overrides.get("total")
        
        if agg_override:
            agg_enabled = agg_override.get('enabled', True)
            agg_time_unit = agg_override.get('time_unit', '1d')
            show_raw_points = agg_override.get('show_raw_points', False)
        else:
            agg_cfg = getattr(self.settings.orchestrator.diagnostics, 'aggregation', None)
            if agg_cfg:
                agg_enabled = getattr(agg_cfg, 'enabled', True)
                agg_time_unit = getattr(agg_cfg, 'time_unit', '1d')
                show_raw_points = getattr(agg_cfg, 'show_raw_points', False)
            else:
                agg_enabled = True
                agg_time_unit = '1d'
                show_raw_points = False
        
        if agg_enabled and len(x_time_use) > 0:
            try:
                from .aggregation import aggregate_timeseries
                agg_cfg = getattr(self.settings.orchestrator.diagnostics, 'aggregation', None)
                if agg_cfg:
                    percentiles = getattr(agg_cfg, 'percentiles', [10, 25, 50, 75, 90])
                else:
                    percentiles = [10, 25, 50, 75, 90]
                
                # Aggregate corrected positions
                bin_centers, stats = aggregate_timeseries(
                    timestamps=x_time_use,
                    values=cp_vals_use,
                    time_unit=agg_time_unit,
                    percentiles=percentiles
                )
                
                # Aggregate residuals
                bin_centers_res, res_stats = aggregate_timeseries(
                    timestamps=x_time_use,
                    values=residuals,
                    time_unit=agg_time_unit,
                    percentiles=percentiles
                )
                
                if len(bin_centers) > 0:
                    aggregated_data = {'bin_centers': bin_centers}
                    aggregated_data.update(stats)
                    aggregated_data['residuals'] = res_stats  # Add aggregated residuals
            except Exception as e:
                log.warning(f"Aggregation failed for total: {e}")
                aggregated_data = None

        # Call plot_residual_composite with overlays
        try:
            out_path = plot_residual_composite(
                asset="total",
                ref_mode="aggregated",
                timestamps=list(x_time_use),
                corrected_positions=cp_vals_use,
                fitted=fitted,
                residuals=residuals,
                sigma=float(sigma),
                k=float(k),
                residual_now=float(r_now),
                triggered=int(1 if ((thr_high is not None and r_now > thr_high) or (sigma > 0 and r_now > k * sigma)) else 0),
                out_dir=str(self.settings.orchestrator.diagnostics.dir),
                include_sigma_band=bool(self.settings.orchestrator.diagnostics.include_sigma_band),
                include_k_sigma_band=bool(self.settings.orchestrator.diagnostics.include_k_sigma_band),
                lookback_hours=eff_lookback,
                hist_samples_per_bin=int(getattr(self.settings.orchestrator.diagnostics, 'hist_samples_per_bin', 10)),
                threshold_mode=str(getattr(dg, 'threshold_mode', 'stddev')),
                central_confidence=float(getattr(dg, 'central_confidence', 0.68)),
                thr_low=thr_low,
                thr_high=thr_high,
                yaxis_mode=yaxis_mode_val,
                percent_base='fit_first',
                deposit_timestamps=deposit_timestamps_list if deposit_timestamps_list else None,
                withdrawal_timestamps=withdrawal_timestamps_list if withdrawal_timestamps_list else None,
                trend_center=None,
                trend_band_lo=None,
                trend_band_hi=None,
                decision_center=None,
                decision_band_lo=None,
                decision_band_hi=None,
                aggregated_data=aggregated_data,  # Phase E: Pass aggregation data
                show_raw_points=show_raw_points,  # Phase E: Control raw points visibility
            )
            with open(out_path, 'rb') as f:
                data_png = f.read()
            return base64.b64encode(data_png).decode('ascii'), None, model_gains_metrics
        except Exception as e:
            # If composite fails entirely, return None (dashboard will show no image)
            return None, str(e), None

    def _build_chart_b64(self, asset: str, view: str, source: str) -> tuple[Optional[str], Optional[str], Optional[Dict[str, float]]]:
        # Special handling for "total" pseudo-asset
        if asset.lower() == "total":
            if view in ("rate_usd", "rate_ada"):
                return None, "'total' is not available for rate views", None
            return self._build_total_chart_b64(view, source)
        
        try:
            import io
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            import numpy as np
            from datetime import timedelta
            import matplotlib.dates as mdates
        except Exception as e:
            return None, str(e), None

        # Fetch data using existing readers/calculators
        cfg = self.settings.client
        reader = GreptimeReader(cfg.greptime, cfg.table_asset_prefix)
        # Resolve display asset to Greptime symbol if possible
        resolved_asset = asset
        try:
            resolver = Resolver(greptime_reader=reader)
            _mid, sym = resolver.resolve_asset(asset)
            if sym:
                resolved_asset = sym
        except Exception:
            resolved_asset = asset
        dr = getattr(cfg, 'date_range', None)
        
        # Phase B: Determine effective plot range and data fetch range
        plot_override = self._plot_range_overrides.get(asset)
        gains_ref_mode = 'alert_driven'  # default
        
        if plot_override:
            # User has specified a custom plot range via dashboard
            plot_range = DateRange(
                start=plot_override['start'],
                end=plot_override['end']
            )
            gains_ref_mode = plot_override.get('gains_reference', 'alert_driven')
        else:
            # Use config default
            dbg_cfg = self.settings.orchestrator.diagnostics
            plot_cfg = getattr(dbg_cfg, 'plot_range', None)
            if plot_cfg:
                plot_range = plot_cfg.resolve(dr)
            else:
                plot_range = dr
        
        # Determine data fetch range (may be expanded beyond plot range)
        data_range_effective = self._expanded_data_ranges.get(asset, dr)
        
        # Use expanded range for data fetching, plot range for final filtering
        dr_fetch = data_range_effective
        
        # Import adapters and types needed for all views
        from . import io_adapters as ioa
        from .io_adapters import DataSourceName as DataSourceNameT
        from typing import cast
        
        # Initialize variables
        units_series = None
        price_series = None
        pos_ts = []
        pos_vals = []
        notice_msg = None
        log = logging.getLogger(__name__)
        
        # Extract configuration used by all views
        dg = self.settings.orchestrator.decision_gate
        dbg = self.settings.orchestrator.diagnostics
        
        # Initialize transaction markers (will be populated by position-based views)
        deposit_timestamps_list = []
        withdrawal_timestamps_list = []
        
        # Initialize outputs
        x_time_use = np.array([])
        cp_vals_use = np.array([])
        
        # View-specific data loading
        # Position-based views (raw, corrected, gains_pct) need units and price data
        # Rate views (rate_usd, rate_ada) only need price/rate data
        #log.info(f"[RATE_DEBUG] _build_chart_b64: asset={resolved_asset}, view={view}, source={source}")
        if view in ('raw', 'corrected', 'gains_pct'):
            #log.info(f"[RATE_DEBUG] Entering position-based view block (raw/corrected/gains_pct)")
            # Build USD position series from units * selected price source (with fallback)
            units_series = reader.fetch_asset_units_series(resolved_asset, dr_fetch)
            price_series = ioa.get_price_series(resolved_asset, cast(DataSourceNameT, source), cfg, dr_fetch)
            
            # Duty cycle check and fallback for position views
            norm = getattr(self, '_norm', None)
            prices_norm = getattr(norm, 'prices', None) if norm is not None else None
            if prices_norm is not None:
                duty_thr = float(getattr(prices_norm, 'duty_cycle_threshold', 0.9))
            else:
                try:
                    duty_thr = float(getattr(getattr(getattr(self.settings.orchestrator, 'prices_v2', None), 'duty_cycle_threshold', 0.9), '__float__', lambda: 0.9)())
                except Exception:
                    duty_thr = 0.9
            dc = ioa.compute_duty_cycle(price_series, dr_fetch)
            if dc < duty_thr and source != "greptime(liqwid)":
                fb = "greptime(liqwid)"
                price_fb = ioa.get_price_series(resolved_asset, cast(DataSourceNameT, fb), cfg, dr_fetch)
                if price_fb and price_fb.series:
                    notice_msg = f"Price source '{source}' had low duty cycle ({dc:.0%}); fell back to Greptime (liqwid)."
                    log.warning(f"Duty cycle {dc:.3f} below threshold for {resolved_asset} using {source}; falling back to greptime(liqwid)")
                    price_series = price_fb
            
            # Validate for position-based views (units are required)
            if not (units_series and units_series.series):
                log.warning(f"[RATE_DEBUG] Position view validation failed: No units series for {resolved_asset}")
                return None, "No units series", None
            if not (price_series and price_series.series):
                log.warning(f"[RATE_DEBUG] Position view validation failed: No price series for {resolved_asset} from {source}")
                return None, "No price series for selected source", None
            #log.info(f"[RATE_DEBUG] Position view validation passed: units={len(units_series.series)} points, prices={len(price_series.series)} points")
            
            # Compute positions (units * price)
            ts_units = sorted(units_series.series.keys())
            ts_price = sorted(price_series.series.keys())
            tb_union = sorted(set(ts_units) | set(ts_price))
            units_vals = [float(units_series.series[t]) for t in ts_units]
            price_vals = [float(price_series.series[t]) for t in ts_price]
            units_interp = interpolate_positions_on_timebase(ts_units, units_vals, np.array(tb_union), 'linear')
            price_interp = interpolate_positions_on_timebase(ts_price, price_vals, np.array(tb_union), 'linear')
            pos_ts = list(tb_union)
            pos_vals = list((np.array(units_interp) * np.array(price_interp)).tolist())
        
        # Rate views will load their own data in view-specific blocks below
        # (No units required, only price/rate data)
        
        # For raw view: treat raw positions as "corrected" (no transaction adjustments)
        if view == 'raw':
            x_time_use = np.array(pos_ts)
            cp_vals_use = np.array(pos_vals, dtype=float)
            # Fetch transactions for markers only
            txs = reader.fetch_transactions(
                asset_symbol=resolved_asset,
                deposits_prefix=cfg.deposits_prefix,
                withdrawals_prefix=cfg.withdrawals_prefix,
                date_range=dr_fetch,
            )
            deposit_timestamps_list = [tx.timestamp for tx in txs if tx.transaction_type == 'deposit']
            withdrawal_timestamps_list = [tx.timestamp for tx in txs if tx.transaction_type == 'withdrawal']
        elif view in ('corrected', 'gains_pct'):
            # Build corrected positions (gains + P_t0) for corrected and gains_pct views
            txs = reader.fetch_transactions(
                asset_symbol=resolved_asset,
                deposits_prefix=cfg.deposits_prefix,
                withdrawals_prefix=cfg.withdrawals_prefix,
                date_range=dr_fetch,
            )
            deposit_timestamps_list = [tx.timestamp for tx in txs if tx.transaction_type == 'deposit']
            withdrawal_timestamps_list = [tx.timestamp for tx in txs if tx.transaction_type == 'withdrawal']
            tb, interp_pos, _d_cdf, _w_cdf, gains = calculate_correct_gains(
                position_timestamps=pos_ts,
                position_values=pos_vals,
                transactions=txs,
                reference_time_index=0,
                interpolation_method='linear',
                alignment_method=str(self.settings.client.alignment_method),
            )
            # Align to original position timestamps
            tb_set = set(pos_ts)
            mask = np.array([ts in tb_set for ts in tb])
            ts_plot = np.array([ts for ts in tb])[mask]
            gains_plot = np.array(gains)[mask]
            P_t0 = float(interp_pos[0]) if len(interp_pos) > 0 else 0.0
            corrected = gains_plot + P_t0
            x_time_use = ts_plot
            cp_vals_use = corrected
        elif view == 'rate_usd':
            # Change-rate (USD): use selected USD source as time series
            #log.info(f"[RATE_DEBUG] Entering rate_usd view block for {resolved_asset}")
            #log.info(f"[RATE_DEBUG] Calling get_change_rate_series_usd with source={source}, table_prefix={cfg.table_asset_prefix}")
            rate_series = ioa.get_change_rate_series_usd(resolved_asset, cast(DataSourceNameT, source), cfg, dr_fetch)
            #log.info(f"[RATE_DEBUG] get_change_rate_series_usd returned: {rate_series}")
            if rate_series and rate_series.series:
                log.info(f"[RATE_DEBUG] rate_usd series has {len(rate_series.series)} data points")
            else:
                log.warning(f"[RATE_DEBUG] rate_usd series is None or empty for {resolved_asset} from {source}")
                return None, "No USD rate series", None
            x_time_use = np.array(sorted(rate_series.series.keys()))
            cp_vals_use = np.array([float(rate_series.series[t]) for t in x_time_use], dtype=float)
            #log.info(f"[RATE_DEBUG] Successfully loaded rate_usd data: {len(x_time_use)} points, values range [{cp_vals_use.min():.4f}, {cp_vals_use.max():.4f}]")
            deposit_timestamps_list = []
            withdrawal_timestamps_list = []
        elif view == 'rate_ada':
            # Change-rate (ADA): minswap price_usd / ada_usd
            #log.info(f"[RATE_DEBUG] Entering rate_ada view block for {resolved_asset}")
            rate_series = ioa.get_change_rate_series_ada(resolved_asset, cfg, dr_fetch)
            if not rate_series or not rate_series.series:
                log.warning(f"[RATE_DEBUG] rate_ada series is None or empty for {resolved_asset}")
                return None, "No ADA rate series", None
            x_time_use = np.array(sorted(rate_series.series.keys()))
            cp_vals_use = np.array([float(rate_series.series[t]) for t in x_time_use], dtype=float)
            log.info(f"[RATE_DEBUG] Successfully loaded rate_ada data: {len(x_time_use)} points")
            deposit_timestamps_list = []
            withdrawal_timestamps_list = []

        # Phase B: Apply plot range filter (after data fetching, before lookback window)
        # This filters the data to the user-requested visualization range
        if plot_range and plot_range.start and plot_range.end and len(x_time_use) > 0:
            mask_plot = (x_time_use >= plot_range.start) & (x_time_use <= plot_range.end)
            x_time_use = x_time_use[mask_plot]
            cp_vals_use = cp_vals_use[mask_plot]
            # Also filter transaction markers to plot range
            deposit_timestamps_list = [ts for ts in deposit_timestamps_list if plot_range.start <= ts <= plot_range.end]
            withdrawal_timestamps_list = [ts for ts in withdrawal_timestamps_list if plot_range.start <= ts <= plot_range.end]
            log.info(f"Applied plot range filter: {len(x_time_use)} points in range [{plot_range.start}, {plot_range.end}]")

        # Apply optional lookback window for fit
        eff_lookback = dbg.lookback_hours_override if getattr(dbg, 'lookback_hours_override', None) is not None else getattr(dg, 'lookback_hours', None)
        if eff_lookback is not None and len(x_time_use) > 0:
            try:
                t_cut = x_time_use[-1] - timedelta(hours=float(eff_lookback))
                mask_lkb = x_time_use >= t_cut
                if mask_lkb.sum() >= max(int(dg.min_points), int(dg.polynomial_order) + 1):
                    x_time_use = x_time_use[mask_lkb]
                    cp_vals_use = cp_vals_use[mask_lkb]
            except Exception:
                pass

        # Determine model order: prefer per-asset smoothing override; fallback to gate's polynomial_order
        try:
            sm = cfg.output.smoothing.get_config_for_asset(asset)
            poly_order = int(getattr(sm, 'polynomial_order', getattr(dg, 'polynomial_order', 2)))
        except Exception:
            poly_order = int(getattr(dg, 'polynomial_order', 2))

        # Fit and residuals
        if getattr(dg, 'method', 'polynomial_fit') == 'median':
            # Flat median baseline
            if len(cp_vals_use) >= int(dg.min_points):
                med = float(np.median(cp_vals_use)) if len(cp_vals_use) else 0.0
                fitted = np.full_like(cp_vals_use, med, dtype=float)
            else:
                fitted = np.full_like(cp_vals_use, float(np.mean(cp_vals_use)) if len(cp_vals_use) else 0.0, dtype=float)
        else:
            # Polynomial fit of selected order
            if len(cp_vals_use) >= max(int(dg.min_points), poly_order + 1):
                t0_base = x_time_use[0]
                x_hours = np.array([(ts - t0_base).total_seconds() / 3600.0 for ts in x_time_use], dtype=float)
                coeffs = np.polyfit(x_hours, cp_vals_use, poly_order)
                poly = np.poly1d(coeffs)
                fitted = poly(x_hours)
            else:
                fitted = np.full_like(cp_vals_use, float(np.mean(cp_vals_use)) if len(cp_vals_use) else 0.0, dtype=float)
        residuals = cp_vals_use - fitted

        # Sigma/thresholds
        try:
            if bool(getattr(dg, 'exclude_last_for_sigma', True)) and len(residuals) > 1:
                res_est = residuals[:-1]
            else:
                res_est = residuals
        except Exception:
            res_est = residuals
        sigma = float(np.std(res_est, ddof=0)) if len(res_est) > 0 else 0.0
        thr_low = None
        thr_high = None
        if str(getattr(dg, 'threshold_mode', 'stddev')).lower() == 'percentile' and len(res_est) > 1:
            try:
                c = float(getattr(dg, 'central_confidence', 0.68))
                p_low = max(0.0, (1.0 - c) / 2.0)
                p_high = min(1.0, 1.0 - p_low)
                q = np.quantile(res_est, [p_low, p_high])
                thr_low = float(q[0])
                thr_high = float(q[1])
            except Exception:
                thr_low = None
                thr_high = None
        r_now = float(residuals[-1]) if len(residuals) else 0.0
        k = float(getattr(dg, 'k_sigma', 2.0))

        # Produce composite plot file (percent mode for gains_pct, absolute for raw/corrected)
        try:
            dec_obj = self.latest_decisions.get(asset, None)
            ref_mode_val = getattr(dec_obj, 'ref_mode', None) if dec_obj is not None else None
            # Set y-axis mode: 'percent' for gains_pct, 'rate' for rate views, 'absolute' for position views
            if view == 'gains_pct':
                yaxis_mode_val = 'percent'
            elif view in ('rate_usd', 'rate_ada'):
                yaxis_mode_val = 'rate'
            else:
                yaxis_mode_val = 'absolute'
            #log.info(f"[RATE_DEBUG] yaxis_mode_val={yaxis_mode_val} for view={view}")

            # Calculate Model Gains (Trend Indicator)
            model_gains_metrics = {}
            if len(x_time_use) > 1:
                # Calculate interval
                t_start = x_time_use[0]
                t_end = x_time_use[-1]
                interval_days = (t_end - t_start).total_seconds() / 86400.0
                model_gains_metrics["interval_days"] = float(interval_days)

                # Calculate Trend Gains using normalized config
                norm = getattr(self, '_norm', None)
                ti_cfg = getattr(norm.analysis, 'trend_indicator', None) if norm is not None else None
                #log.info(f"[GAINS_DEBUG] ti_cfg exists: {ti_cfg is not None}, enabled: {getattr(ti_cfg, 'enabled', False) if ti_cfg else 'N/A'}, method: {getattr(ti_cfg, 'method', 'N/A') if ti_cfg else 'N/A'}")
                if ti_cfg and getattr(ti_cfg, 'enabled', False) and getattr(ti_cfg, 'method', 'polynomial_fit') == 'polynomial_fit':
                    try:
                        trend_poly_order = int(getattr(ti_cfg, 'polynomial_order', 2))
                        #log.info(f"[GAINS_DEBUG] Calculating trend gains: poly_order={trend_poly_order}, data_points={len(cp_vals_use)}")
                        if len(cp_vals_use) >= trend_poly_order + 1:
                            t0_base = x_time_use[0]
                            x_hours = np.array([(ts - t0_base).total_seconds() / 3600.0 for ts in x_time_use], dtype=float)
                            coeffs = np.polyfit(x_hours, cp_vals_use, trend_poly_order)
                            poly = np.poly1d(coeffs)
                            trend_fitted = poly(x_hours)
                            
                            # Calculate gains from trend fit
                            # Normalize if needed (percent mode)
                            denom = 1.0
                            if yaxis_mode_val == 'percent':
                                # Replicate normalization logic from diagnostics.py for consistency
                                # base='fit_first' logic: first valid point of trend fit
                                valid_pos = np.where(np.isfinite(trend_fitted) & (np.abs(trend_fitted) > 0))[0]
                                if valid_pos.size > 0:
                                    denom = float(trend_fitted[valid_pos[0]])
                                else:
                                    denom = 1.0
                            
                            y_fit_plot = (trend_fitted / denom) * (100.0 if yaxis_mode_val == 'percent' else 1.0)
                            model_gains = y_fit_plot[-1] - y_fit_plot[0]
                            model_gains_per_day = model_gains / interval_days if interval_days > 0 else 0.0
                            
                            model_gains_metrics["model_gains_per_month"] = float(model_gains_per_day * 30)
                            model_gains_metrics["model_gains_per_year"] = float(model_gains_per_day * 365)
                            #log.info(f"[GAINS_DEBUG] Model gains calculated: per_month={model_gains_metrics['model_gains_per_month']:.2f}, per_year={model_gains_metrics['model_gains_per_year']:.2f}")
                        else:
                            log.warning(f"[GAINS_DEBUG] Insufficient data points: {len(cp_vals_use)} < {trend_poly_order + 1}")
                    except Exception as e:
                        log.error(f"[GAINS_DEBUG] Error calculating trend gains: {e}")
                        pass

            # Compute overlays: trend (informational) and decision (basis-specific)
            trend_center = None
            trend_lo = None
            trend_hi = None
            decision_center = None
            decision_lo = None
            decision_hi = None

            # Helper to compute moving average
            def _moving_average(vals: np.ndarray, window_hours: float, ts_arr: np.ndarray) -> Optional[np.ndarray]:
                try:
                    if vals.size == 0 or ts_arr.size == 0 or window_hours is None:
                        return None
                    # Convert window to sample count by approximating median dt
                    dts = np.diff(ts_arr).astype('timedelta64[s]').astype(float)
                    if dts.size == 0:
                        return vals.copy()
                    med_dt_s = float(np.median(dts)) if np.isfinite(np.median(dts)) and np.median(dts) > 0 else float(dts[0])
                    if med_dt_s <= 0:
                        return None
                    w = max(1, int(round((window_hours * 3600.0) / med_dt_s)))
                    if w <= 1:
                        return vals.copy()
                    kernel = np.ones(w, dtype=float) / float(w)
                    return np.convolve(vals, kernel, mode='same')
                except Exception:
                    return None

            # Build trend overlay (always when enabled)
            tr_cfg_legacy = getattr(self.settings.orchestrator, 'trend_indicator', None)
            # Prefer normalized trend view when available
            norm = getattr(self, '_norm', None)
            tiv = None
            try:
                if norm is not None and getattr(norm, 'analysis', None) is not None:
                    tiv = getattr(norm.analysis, 'trend_indicator', None)
            except Exception:
                tiv = None
            enabled = bool(getattr(tiv, 'enabled', getattr(tr_cfg_legacy, 'enabled', False)))
            if enabled:
                # Extract parameters from normalized view if present; otherwise fallback to legacy
                method = str(getattr(tiv, 'method', getattr(tr_cfg_legacy, 'method', 'polynomial_fit'))).lower()
                window_type = str(getattr(tiv, 'window_type', 'polynomial')).lower()
                w_h = float(getattr(tiv, 'window_size_hours', getattr(tr_cfg_legacy, 'window_size_hours', 24.0)))
                tr_order = int(getattr(tiv, 'polynomial_order', getattr(tr_cfg_legacy, 'polynomial_order', 2)))
                sigma_frac = float(getattr(tiv, 'gaussian_kde_sigma_fraction', 0.3))

                # Apply per-asset overrides when present in normalized view
                try:
                    pa = getattr(tiv, 'per_asset', {}) or {}
                    if isinstance(pa, dict):
                        ov = pa.get(str(asset).lower())
                        if isinstance(ov, dict):
                            if 'method' in ov and ov['method']:
                                method = str(ov['method']).lower()
                            if 'window_type' in ov and ov['window_type']:
                                window_type = str(ov['window_type']).lower()
                            if 'window_size_hours' in ov and ov['window_size_hours'] is not None:
                                w_h = float(ov['window_size_hours'])
                            if 'polynomial_order' in ov and ov['polynomial_order'] is not None:
                                tr_order = int(ov['polynomial_order'])
                            if 'gaussian_kde_sigma_fraction' in ov and ov['gaussian_kde_sigma_fraction'] is not None:
                                sigma_frac = float(ov['gaussian_kde_sigma_fraction'])
                except Exception:
                    pass

                # Helper for Gaussian / boxcar smoothing based on window size and median dt
                def _median_dt_seconds(ts_arr: np.ndarray) -> Optional[float]:
                    try:
                        if ts_arr.size < 2:
                            return None
                        dts = np.diff(ts_arr).astype('timedelta64[s]').astype(float)
                        if dts.size == 0:
                            return None
                        med = float(np.median(dts))
                        return med if med > 0 else None
                    except Exception:
                        return None

                def _gaussian_smooth(vals: np.ndarray, window_hours: float, ts_arr: np.ndarray, sigma_fraction: float) -> Optional[np.ndarray]:
                    try:
                        med_dt_s = _median_dt_seconds(ts_arr)
                        if med_dt_s is None:
                            return None
                        window_pts = float(window_hours * 3600.0) / med_dt_s
                        if window_pts <= 1.0:
                            return vals.copy()
                        sigma_pts = max(1.0, sigma_fraction * window_pts)
                        # Kernel size ~ 6 sigma, odd length
                        k = int(max(3, round(6.0 * sigma_pts)))
                        if k % 2 == 0:
                            k += 1
                        center = k // 2
                        x = np.arange(k, dtype=float)
                        kernel = np.exp(-0.5 * ((x - center) / sigma_pts) ** 2)
                        kernel /= np.sum(kernel)
                        return np.convolve(vals, kernel, mode='same')
                    except Exception:
                        return None

                def _boxcar_smooth(vals: np.ndarray, window_hours: float, ts_arr: np.ndarray) -> Optional[np.ndarray]:
                    try:
                        med_dt_s = _median_dt_seconds(ts_arr)
                        if med_dt_s is None:
                            return None
                        window_pts = int(round((window_hours * 3600.0) / med_dt_s))
                        if window_pts <= 1:
                            return vals.copy()
                        kernel = np.ones(window_pts, dtype=float) / float(window_pts)
                        return np.convolve(vals, kernel, mode='same')
                    except Exception:
                        return None

                # Decide visualization path
                if method == 'moving_average':
                    tr = _moving_average(cp_vals_use, w_h, x_time_use)
                    if tr is not None:
                        trend_center = tr
                elif method == 'polynomial_fit' and window_type == 'polynomial':
                    if len(cp_vals_use) >= tr_order + 1:
                        t0b = x_time_use[0]
                        xh = np.array([(t - t0b).total_seconds() / 3600.0 for t in x_time_use], dtype=float)
                        try:
                            coeffs_tr = np.polyfit(xh, cp_vals_use, tr_order)
                            poly_tr = np.poly1d(coeffs_tr)
                            trend_center = poly_tr(xh)
                        except Exception:
                            trend_center = None
                else:
                    # window_type overrides for smoothing-based visualization
                    if window_type == 'gaussian':
                        tr = _gaussian_smooth(cp_vals_use, w_h, x_time_use, sigma_frac)
                        if tr is not None:
                            trend_center = tr
                    elif window_type == 'boxcar':
                        tr = _boxcar_smooth(cp_vals_use, w_h, x_time_use)
                        if tr is not None:
                            trend_center = tr
                    elif window_type == 'none':
                        trend_center = None
                # Trend band: simple residual std around trend for display (not decision)
                if trend_center is not None and len(trend_center) == len(cp_vals_use):
                    try:
                        res_tr = cp_vals_use - trend_center
                        std_tr = float(np.std(res_tr)) if res_tr.size > 1 else 0.0
                        if std_tr > 0:
                            trend_lo = trend_center - std_tr
                            trend_hi = trend_center + std_tr
                    except Exception:
                        pass

            # Build decision overlay only on basis-matching view
            basis = str(getattr(dg, 'basis', 'corrected_position')).lower()
            basis_matches_view = (
                (basis == 'change_rate_usd' and view == 'rate_usd') or
                (basis == 'corrected_position' and view in ('corrected', 'gains_pct') and yaxis_mode_val != 'percent')
            )
            if getattr(dg, 'enabled', False) and basis_matches_view:
                meth = str(getattr(dg, 'method', 'polynomial_fit')).lower()
                if meth == 'median':
                    if len(cp_vals_use) >= int(dg.min_points):
                        med = float(np.median(cp_vals_use))
                        decision_center = np.full_like(cp_vals_use, med, dtype=float)
                    else:
                        decision_center = np.full_like(cp_vals_use, float(np.mean(cp_vals_use)) if len(cp_vals_use) else 0.0, dtype=float)
                else:
                    ord_dec = int(getattr(dg, 'polynomial_order', 1))
                    if len(cp_vals_use) >= max(int(dg.min_points), ord_dec + 1):
                        t0b = x_time_use[0]
                        xh = np.array([(t - t0b).total_seconds() / 3600.0 for t in x_time_use], dtype=float)
                        try:
                            coeffs_dc = np.polyfit(xh, cp_vals_use, ord_dec)
                            poly_dc = np.poly1d(coeffs_dc)
                            decision_center = poly_dc(xh)
                        except Exception:
                            decision_center = None
                # Decision band mirrors thresholding
                if decision_center is not None and len(decision_center) == len(cp_vals_use):
                    try:
                        # Reuse previously computed res_est/sigma or percentile thresholds
                        if str(getattr(dg, 'threshold_mode', 'stddev')).lower() == 'percentile' and thr_low is not None and thr_high is not None:
                            decision_lo = decision_center + float(thr_low)
                            decision_hi = decision_center + float(thr_high)
                        else:
                            if np.isfinite(sigma) and sigma > 0:
                                kk = float(getattr(dg, 'k_sigma', 2.0))
                                decision_lo = decision_center - kk * float(sigma)
                                decision_hi = decision_center + kk * float(sigma)
                    except Exception:
                        pass
            
            # Phase E: Aggregation support
            # Check if aggregation should be applied (from config or dashboard override)
            aggregated_data = None
            show_raw_points = True
            agg_override = self._aggregation_overrides.get(asset)
            
            if agg_override:
                # User dashboard override takes precedence
                agg_enabled = agg_override.get('enabled', False)
                agg_time_unit = agg_override.get('time_unit', '1d')
                show_raw_points = agg_override.get('show_raw_points', False)
            else:
                # Use config default
                agg_cfg = getattr(self.settings.orchestrator.diagnostics, 'aggregation', None)
                if agg_cfg:
                    agg_enabled = getattr(agg_cfg, 'enabled', True)
                    agg_time_unit = getattr(agg_cfg, 'time_unit', '1d')
                    show_raw_points = getattr(agg_cfg, 'show_raw_points', False)
                else:
                    agg_enabled = True
                    agg_time_unit = '1d'
                    show_raw_points = False
            
            # Apply aggregation if enabled and data is suitable
            if agg_enabled and len(x_time_use) > 0:
                try:
                    from .aggregation import aggregate_timeseries
                    
                    # Get percentiles from config or use defaults
                    agg_cfg = getattr(self.settings.orchestrator.diagnostics, 'aggregation', None)
                    if agg_cfg:
                        percentiles = getattr(agg_cfg, 'percentiles', [10, 25, 50, 75, 90])
                    else:
                        percentiles = [10, 25, 50, 75, 90]
                    
                    # Aggregate the corrected positions
                    bin_centers, stats = aggregate_timeseries(
                        timestamps=x_time_use,
                        values=cp_vals_use,
                        time_unit=agg_time_unit,
                        percentiles=percentiles
                    )
                    
                    # Aggregate residuals
                    bin_centers_res, res_stats = aggregate_timeseries(
                        timestamps=x_time_use,
                        values=residuals,
                        time_unit=agg_time_unit,
                        percentiles=percentiles
                    )
                    
                    # Build aggregated_data dict for plot_residual_composite
                    if len(bin_centers) > 0:
                        aggregated_data = {'bin_centers': bin_centers}
                        aggregated_data.update(stats)  # Add p10, p25, p50, p75, p90, count
                        aggregated_data['residuals'] = res_stats  # Add aggregated residuals
                except Exception as e:
                    # If aggregation fails, continue without it
                    log.warning(f"Aggregation failed for {asset}: {e}")
                    aggregated_data = None
            
            # Call plot_residual_composite with overlays
            try:
                out_path = plot_residual_composite(
                    asset=asset,
                    ref_mode=ref_mode_val,
                    timestamps=list(x_time_use),
                    corrected_positions=cp_vals_use,
                    fitted=fitted,
                    residuals=residuals,
                    sigma=float(sigma),
                    k=float(k),
                    residual_now=float(r_now),
                    triggered=int(1 if ((thr_high is not None and r_now > thr_high) or (sigma > 0 and r_now > k * sigma)) else 0),
                    out_dir=str(self.settings.orchestrator.diagnostics.dir),
                    include_sigma_band=bool(self.settings.orchestrator.diagnostics.include_sigma_band),
                    include_k_sigma_band=bool(self.settings.orchestrator.diagnostics.include_k_sigma_band),
                    lookback_hours=eff_lookback,
                    hist_samples_per_bin=int(getattr(self.settings.orchestrator.diagnostics, 'hist_samples_per_bin', 10)),
                    threshold_mode=str(getattr(dg, 'threshold_mode', 'stddev')),
                    central_confidence=float(getattr(dg, 'central_confidence', 0.68)),
                    thr_low=thr_low,
                    thr_high=thr_high,
                    yaxis_mode=yaxis_mode_val,
                    percent_base='fit_first',
                    deposit_timestamps=deposit_timestamps_list if deposit_timestamps_list else None,
                    withdrawal_timestamps=withdrawal_timestamps_list if withdrawal_timestamps_list else None,
                    trend_center=trend_center,
                    trend_band_lo=trend_lo,
                    trend_band_hi=trend_hi,
                    decision_center=decision_center,
                    decision_band_lo=decision_lo,
                    decision_band_hi=decision_hi,
                    aggregated_data=aggregated_data,  # Phase E: Pass aggregation data
                    show_raw_points=show_raw_points,  # Phase E: Control raw points visibility
                )
                with open(out_path, 'rb') as f:
                    data_png = f.read()
                return base64.b64encode(data_png).decode('ascii'), notice_msg, model_gains_metrics
            except Exception as e:
                # If composite fails entirely, return None (dashboard will show no image)
                return None, str(e), None
        except Exception as e:
            return None, str(e), None
