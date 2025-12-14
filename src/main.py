#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Alert Orchestrator main runner.
- Loads settings
- Starts Prometheus /metrics server (prometheus_client)
- Periodically evaluates decisions and updates metrics

Usage examples:
  python -m src.main --help
  python -m src.main --config path/to/orchestrator_config.yaml
  python -m src.main --once  # run one evaluation and exit
  python -m src.main --test-prefix  # enable test mode (writes to test_* tables)
"""
from __future__ import annotations

import time
import threading
import logging
from pathlib import Path
import sys
import argparse
from typing import Optional

from .core.settings import load_settings
from .core.token_registry import load_registry, TokenRegistryError
from .shared.greptime_reader import create_greptime_reader
from .core.alert_logic import evaluate_once
from .core.exporter import MetricsExporter
from .core.config_normalizer import build_normalized_config
from .shared.colored_logging import setup_colored_logging
from .core.housekeeping import cleanup_outputs


def _evaluation_loop(settings, exporter) -> None:
    cfg = settings.client
    reader = create_greptime_reader(cfg.greptime, cfg.table_asset_prefix)
    interval = max(1, int(settings.orchestrator.schedule.interval_minutes)) * 60
    log = logging.getLogger(__name__)

    while True:
        try:
            decisions = evaluate_once(reader, settings)
            exporter.update(decisions)
            # Console summary (privacy-safe): asset -> decision, total wmax, wallet count
            lines = []
            for asset, dec in decisions.items():
                total_wmax = sum(wb.wmax_usd for wb in dec.wmax_usd)
                num_wallets = len(dec.wmax_usd)
                reason = None
                if total_wmax <= 0.0:
                    if num_wallets == 0:
                        reason = "no wallets/data"
                    else:
                        g_val = getattr(dec, 'g_usd', None)
                        if g_val is not None and g_val <= 0:
                            reason = "non-positive gains since t0 (clamped)"
                        elif all((wb.wmax_usd or 0.0) <= 0.0 for wb in dec.wmax_usd):
                            reason = "all wallets clamped to 0"
                if num_wallets > 0:
                    msg = (
                        f"{asset}: decision={dec.decision}, wmax_usd={total_wmax:.2f} "
                        f"({num_wallets} wallet{'s' if num_wallets != 1 else ''})"
                    )
                else:
                    msg = f"{asset}: decision={dec.decision}, wmax_usd={total_wmax:.2f}"
                if reason:
                    msg += f" [reason: {reason}]"
                lines.append(msg)
            log.info("Evaluation: \n" + "\n".join(lines))
        except Exception as e:
            log.exception(f"Evaluation error: {e}")
        time.sleep(interval)


def main(config_path: Optional[str] = None, once: bool = False, no_telemetry: bool = False, log_level: int = logging.INFO, print_config_normalized: bool = False, test_prefix: bool = False) -> None:
    setup_colored_logging(
        level=log_level,
        fmt='%(asctime)s - %(levelname)s - [%(name)s:%(module)s.%(funcName)s:%(lineno)d] - %(message)s'
    )
    base = Path(__file__).resolve().parents[1]
    default_cfg = base / 'config' / 'orchestrator_config.yaml'
    settings = load_settings(str(config_path or default_cfg))

    # Override test_prefix if provided via CLI
    if test_prefix:
        logging.getLogger(__name__).warning(
            "⚠️  TEST MODE: All database writes will use 'test_*' prefixed tables. "
            "Reads will still use normal tables."
        )
        settings.client.greptime.test_prefix = True

    # Optional: print normalized config (config doctor via CLI) and exit
    if print_config_normalized:
        try:
            from dataclasses import asdict
            import json as _json
            norm = build_normalized_config(settings)
            print(_json.dumps(asdict(norm), indent=2, sort_keys=True))
        except Exception as e:
            print(f"Failed to build normalized config: {e}")
            sys.exit(2)
        sys.exit(0)

    # Cleanup generated outputs based on configured expiry (runs once at startup)
    try:
        deleted = cleanup_outputs(settings.orchestrator, base)
        if deleted:
            logging.getLogger(__name__).info(f"Startup housekeeping: deleted {deleted} old output files.")
    except Exception as _:
        # Non-fatal: continue
        pass

    # Load on-disk token registry and validate mappings for configured assets
    try:
        registry_path = base / 'config' / 'token_registry.csv'
        registry = load_registry(str(registry_path))
        ok, missing = registry.validate_assets_present(settings.client.assets)
        if not ok:
            missing_list = ", ".join(missing)
            msg = (
                f"Token registry resolution failed for assets: {missing_list}.\n"
                f"Please update {registry_path} with rows: asset,policy_id,token_name_hex.\n"
                f"You can obtain policy id and token name (hex) from Minswap's token page."
            )
            print(msg)
            sys.exit(1)
    except TokenRegistryError as tre:
        print(f"Failed to load token registry: {tre}")
        sys.exit(1)

    if settings.orchestrator.telemetry.enabled and not once and not no_telemetry:
        exporter = MetricsExporter(settings)
        exporter.start_http()
        t = threading.Thread(target=_evaluation_loop, args=(settings, exporter), daemon=True)
        t.start()
        # Keep main thread alive
        while True:
            time.sleep(3600)
    else:
        # Run a single evaluation and print results if telemetry disabled
        reader = create_greptime_reader(settings.client.greptime, settings.client.table_asset_prefix)
        decisions = evaluate_once(reader, settings)
        for asset, dec in decisions.items():
            total_wmax = sum(wb.wmax_usd for wb in dec.wmax_usd)
            num_wallets = len(dec.wmax_usd)
            reason = None
            if total_wmax <= 0.0:
                if num_wallets == 0:
                    reason = "no wallets/data"
                else:
                    g_val = getattr(dec, 'g_usd', None)
                    if g_val is not None and g_val <= 0:
                        reason = "non-positive gains since t0 (clamped)"
                    elif all((wb.wmax_usd or 0.0) <= 0.0 for wb in dec.wmax_usd):
                        reason = "all wallets clamped to 0"
            if num_wallets > 0:
                msg = (
                    f"{asset}: decision={dec.decision}, wmax_usd={total_wmax:.2f} "
                    f"({num_wallets} wallet{'s' if num_wallets != 1 else ''})"
                )
            else:
                msg = f"{asset}: decision={dec.decision}, wmax_usd={total_wmax:.2f}"
            if reason:
                msg += f" [reason: {reason}]"
            print(msg)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Alert Orchestrator')
    parser.add_argument('--config', type=str, default=None, help='Path to orchestrator_config.yaml')
    parser.add_argument('--once', action='store_true', help='Run one evaluation and exit (no metrics server)')
    parser.add_argument('--no-telemetry', action='store_true', help='Disable metrics server even if enabled in config')
    parser.add_argument('--log-level', type=str, default='INFO', choices=['DEBUG','INFO','WARNING','ERROR','CRITICAL'], help='Logging level')
    parser.add_argument('--print-config-normalized', action='store_true', help='Print normalized config (config doctor) as JSON and exit')
    parser.add_argument('--test-prefix', action='store_true', help='Enable test mode: all writes go to test_* tables (reads still use normal tables)')
    args = parser.parse_args()
    level = getattr(logging, args.log_level.upper(), logging.INFO)
    main(config_path=args.config, once=args.once, no_telemetry=args.no_telemetry, log_level=level, print_config_normalized=args.print_config_normalized, test_prefix=args.test_prefix)
