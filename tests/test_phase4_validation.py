import os
import yaml
from pathlib import Path
import types
import pytest

from pathlib import Path
import yaml
import pytest

from src.core.settings import load_settings, SettingsError
from src.core.config_normalizer import build_normalized_config


def _write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(content)
    return p


def test_v2_endpoint_enforcement_liqwid_requires_endpoint(tmp_path: Path):
    cfg = """
settings:
  timezone: "UTC"
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
  sources: ["liqwid"]
  duty_cycle_threshold: 0.9
  endpoints: {}
  priority_by_logical:
    liqwid: ["greptime(liqwid)", "liqwid"]
analysis:
  trend_indicator:
    enabled: true
  price_compare:
    enabled: true
    sources: ["liqwid"]
runtime:
  telemetry:
    enabled: false
maintenance:
  cleanup:
    enabled: true
    expire_before: "7d"
    paths: ["output"]
    extensions: [".png", ".jpg", ".jpeg", ".svg", ".csv", ".tsv", ".json", ".log"]
"""
    path = _write(tmp_path, cfg)
    # Avoid Greptime network dependency so preflight passes
    import src.shared.greptime_reader as gr
    orig = gr.GreptimeReader.test_connection
    gr.GreptimeReader.test_connection = lambda self: True
    try:
        with pytest.raises(SettingsError) as ei:
            _ = load_settings(path)
        assert "prices.endpoints.liqwid_graphql" in str(ei.value)
    finally:
        gr.GreptimeReader.test_connection = orig


def test_v2_endpoint_enforcement_minswap_requires_endpoint(tmp_path: Path):
    cfg = """
settings:
  timezone: "UTC"
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
  price_compare:
    enabled: true
    sources: ["minswap"]
runtime:
  telemetry:
    enabled: false
"""
    path = _write(tmp_path, cfg)
    # Avoid Greptime network dependency so preflight passes
    import src.shared.greptime_reader as gr
    orig = gr.GreptimeReader.test_connection
    gr.GreptimeReader.test_connection = lambda self: True
    try:
        with pytest.raises(SettingsError) as ei:
            _ = load_settings(path)
        assert "prices.endpoints.minswap_aggregator" in str(ei.value)
    finally:
        gr.GreptimeReader.test_connection = orig


def test_v2_indirection_prices_sources(tmp_path: Path):
    cfg = """
settings:
  timezone: "UTC"
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
  price_compare:
    enabled: true
    sources: "@prices.sources"
runtime:
  telemetry:
    enabled: false
"""
    path = _write(tmp_path, cfg)

    # Monkeypatch GreptimeReader.test_connection to avoid real network during preflight
    import src.shared.greptime_reader as gr
    orig = gr.GreptimeReader.test_connection
    gr.GreptimeReader.test_connection = lambda self: True
    try:
        settings = load_settings(path)
    finally:
        gr.GreptimeReader.test_connection = orig

    # Ensure indirection resolved to prices.sources
    assert settings.orchestrator.analysis_v2 is not None
    pc2 = settings.orchestrator.analysis_v2.price_compare
    assert pc2 is not None and pc2.enabled is True
    assert pc2.sources == ["liqwid", "minswap"]

    # Normalized config should reflect priorities
    norm = build_normalized_config(settings)
    assert norm.prices.priority_by_logical["liqwid"][0] == "greptime(liqwid)"


def test_preflight_fails_without_greptime(tmp_path: Path):
    cfg = """
settings:
  timezone: "UTC"
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
  sources: ["liqwid"]
  duty_cycle_threshold: 0.9
  endpoints: {}
  priority_by_logical:
    liqwid: ["greptime(liqwid)", "liqwid"]
analysis:
  trend_indicator:
    enabled: true
  price_compare:
    enabled: false
runtime:
  telemetry:
    enabled: false
"""
    path = _write(tmp_path, cfg)
    # Force preflight to fail
    import src.shared.greptime_reader as gr
    orig = gr.GreptimeReader.test_connection
    gr.GreptimeReader.test_connection = lambda self: False
    try:
        with pytest.raises(SettingsError) as ei:
            load_settings(path)
        assert "Preflight failed" in str(ei.value)
    finally:
        gr.GreptimeReader.test_connection = orig


def test_repo_yaml_is_v2_only_no_duplicates():
    # Parse repo config to ensure there are no duplicate top-level keys within the same document
    repo_root = Path(__file__).resolve().parents[2]
    cfg_path = repo_root / "alert_orchestrator" / "config" / "orchestrator_config.yaml"
    assert cfg_path.exists(), f"Missing config at {cfg_path}"
    text = cfg_path.read_text()
    data = yaml.safe_load(text)
    assert isinstance(data, dict)
    # Expected top-level keys must be unique
    expected_keys = {"settings", "domain", "data", "prices", "analysis", "runtime", "visualization", "maintenance"}
    assert expected_keys.issubset(set(data.keys()))
    # Ensure only one prices and one analysis block
    assert list(data.keys()).count("prices") == 1
    assert list(data.keys()).count("analysis") == 1
    # Ensure analysis.price_compare uses either list or indirection string
    pc = (data.get("analysis", {}) or {}).get("price_compare", {}) or {}
    assert isinstance(pc, dict)
    sources = pc.get("sources")
    assert (isinstance(sources, list) and all(isinstance(s, str) for s in sources)) or (isinstance(sources, str) and sources.strip() == "@prices.sources")
