from pathlib import Path
from src.core.settings import load_settings
from src.core.config_normalizer import build_normalized_config


def test_load_new_schema_and_normalize(tmp_path: Path):
    cfg = """
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
  endpoints:
    liqwid_graphql: "https://v2.api.liqwid.finance/graphql"
    minswap_aggregator: "https://agg-api.minswap.org"
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
      enabled: true
      basis: "corrected_position"
      method: "median"
      polynomial_order: 1
      threshold_mode: "percentile"
      central_confidence: 0.68
      k_sigma: 2.0
      min_points: 20
      exclude_last_for_sigma: true
      lookback_hours: null
      sigma_epsilon: 1e-6
    safety_factor:
      c: 0.5
  price_compare:
    enabled: true
    sources: "@prices.sources"
    epsilon_mode: "relative"
    tolerance_epsilon: 0.01
    persistence_threshold: 1
    action_on_mismatch: "hold"
    per_asset_overrides: {}
runtime:
  telemetry:
    enabled: false
visualization:
  diagnostics:
    enabled: false
maintenance:
  cleanup:
    enabled: true
    expire_before: "7d"
    paths: ["output"]
    extensions: [".png", ".jpg", ".jpeg", ".svg", ".csv", ".tsv", ".json", ".log"]
"""
    conf = tmp_path / "config.yaml"
    conf.write_text(cfg)

    # Avoid Greptime network dependency during preflight
    import src.shared.greptime_reader as gr
    orig = gr.GreptimeReader.test_connection
    gr.GreptimeReader.test_connection = lambda self: True
    try:
        settings = load_settings(conf)
    finally:
        gr.GreptimeReader.test_connection = orig

    assert settings.orchestrator.prices_v2 is not None
    assert settings.orchestrator.analysis_v2 is not None
    norm = build_normalized_config(settings)
    # normalized prices priorities are carried through
    assert "liqwid" in norm.prices.priority_by_logical
    assert norm.analysis.trend_indicator.enabled is True
