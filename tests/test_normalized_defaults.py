import json
from pathlib import Path
from src.core.settings import load_settings, SettingsError
from src.core.config_normalizer import build_normalized_config


def write_yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(content)
    return p


def test_default_provider_from_v2_prices(tmp_path):
    cfg = '''
settings:
  timezone: "Asia/Tokyo"
  currency: "usd"
domain:
  assets: ["usdc"]
data:
  databases:
    greptime:
      host: "http://localhost"
      port: 4000
      database: "liqwid"
      timeout: 5
  datasets:
    transactions:
      alignment_method: "detect_spike"
      sources:
        liqwid:
          table_asset_prefix: "liqwid_supply_positions_"
          deposits_prefix: "liqwid_deposits_"
          withdrawals_prefix: "liqwid_withdrawals_"
  date_range:
    start: "2025-10-04"
    end: null
prices:
  sources: ["liqwid", "minswap"]
  duty_cycle_threshold: 0.9
  endpoints: {}
  priority_by_logical:
    liqwid: ["greptime(liqwid)", "liqwid"]
    minswap: ["greptime(minswap)", "minswap"]
analysis:
  trend_indicator:
    enabled: true
    method: "polynomial_fit"
    polynomial_order: 2
    window_size_hours: 24.0
    window_type: "polynomial"
    gaussian_kde_sigma_fraction: 0.3
  decision:
    reference:
      keyword: "alert_driven_withdrawal"
      fallback: "data_range"
    gate:
      enabled: false
runtime:
  telemetry:
    enabled: false
visualization:
  diagnostics:
    enabled: false
maintenance:
  cleanup:
    enabled: false
'''
    path = write_yaml(tmp_path, cfg)
    # Avoid Greptime network dependency during preflight
    import src.shared.greptime_reader as gr
    orig = gr.GreptimeReader.test_connection
    gr.GreptimeReader.test_connection = lambda self: True
    try:
        settings = load_settings(path)
    finally:
        gr.GreptimeReader.test_connection = orig
    norm = build_normalized_config(settings)
    assert norm.prices.priority_by_logical["liqwid"][0] == "greptime(liqwid)"


def test_legacy_schema_rejected_in_phase6(tmp_path):
    cfg = '''
client:
  greptime:
    host: "http://localhost"
    port: 4000
    database: "liqwid"
    timeout: 5
  transactions:
    assets: ["usdc"]
orchestrator:
  telemetry:
    enabled: false
  decision_price_sources: ["liqwid", "minswap"]
  telemetry:
    price_source_priority:
      liqwid: ["greptime(liqwid)", "liqwid"]
      minswap: ["greptime(minswap)", "minswap"]
'''
    path = write_yaml(tmp_path, cfg)
    # Avoid Greptime network dependency during preflight
    import src.shared.greptime_reader as gr
    orig = gr.GreptimeReader.test_connection
    gr.GreptimeReader.test_connection = lambda self: True
    try:
        import pytest
        with pytest.raises(SettingsError):
            _ = load_settings(path)
    finally:
        gr.GreptimeReader.test_connection = orig
