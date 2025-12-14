#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Configuration System for Liqwid Client Aggregator
Handles YAML configuration loading, validation, and type conversion.
"""

import yaml
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any
import logging


@dataclass
class GreptimeConnConfig:
    """GreptimeDB connection configuration"""
    host: str = "http://localhost"
    port: int = 4000
    database: str = "liqwid"
    timeout: int = 10
    test_prefix: bool = False  # If True, writes go to test_* tables (reads still use normal tables)
    
    def __post_init__(self):
        """Validate connection parameters"""
        if not self.host:
            raise ValueError("GreptimeDB host cannot be empty")
        if not (1 <= self.port <= 65535):
            raise ValueError("Port must be between 1 and 65535")
        if not self.database:
            raise ValueError("Database name cannot be empty")
        if self.timeout <= 0:
            raise ValueError("Timeout must be positive")


@dataclass
class DateRange:
    """Date range configuration for queries"""
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    
    def __post_init__(self):
        """Validate date range"""
        if self.start and self.end and self.start >= self.end:
            raise ValueError("Start date must be before end date")


@dataclass
class SmoothingMethod:
    """Base smoothing configuration for a single method"""
    window_type: str = "gaussian"           # "gaussian", "boxcar", "none", "polynomial"
    window_size_hours: float = 24.0         # Time-based window size for convolution methods
    gaussian_std: float = 0.3               # Standard deviation factor for Gaussian (0.0-1.0)
    polynomial_order: int = 2               # Polynomial order for polynomial method
    
    def __post_init__(self):
        """Validate smoothing method configuration"""
        valid_types = ["gaussian", "boxcar", "none", "polynomial"]
        if self.window_type not in valid_types:
            raise ValueError(f"window_type must be one of {valid_types}")
        if self.window_size_hours <= 0:
            raise ValueError("window_size_hours must be positive")
        if self.gaussian_std <= 0 or self.gaussian_std > 1.0:
            raise ValueError("gaussian_std must be between 0 and 1.0")
        if self.polynomial_order < 1:
            raise ValueError("polynomial_order must be >= 1")


@dataclass
class AssetSmoothingConfig:
    """Asset-specific smoothing configuration with defaults and overrides"""
    default: SmoothingMethod = field(default_factory=SmoothingMethod)
    asset_overrides: Dict[str, SmoothingMethod] = field(default_factory=dict)
    
    def get_config_for_asset(self, asset_symbol: str) -> SmoothingMethod:
        """
        Get effective smoothing configuration for specific asset
        
        Args:
            asset_symbol: Asset symbol (case-insensitive)
            
        Returns:
            SmoothingMethod configuration for the asset
        """
        normalized_symbol = asset_symbol.lower().strip()
        return self.asset_overrides.get(normalized_symbol, self.default)
    
    def __post_init__(self):
        """Validate asset smoothing configuration"""
        # Ensure all asset override keys are lowercase
        self.asset_overrides = {
            key.lower().strip(): value 
            for key, value in self.asset_overrides.items()
        }


# Backward compatibility alias
SmoothingConfig = SmoothingMethod


@dataclass
class OutputConfig:
    """Output configuration for reports and charts"""
    dir: str = "client/output"
    time_format: str = "iso"  # "iso" or "human"
    dpi: int = 300
    chart_width: float = 12.0
    chart_height: float = 8.0
    include_charts: bool = True
    include_gains_charts: bool = True
    smoothing: AssetSmoothingConfig = field(default_factory=AssetSmoothingConfig)
    
    def __post_init__(self):
        """Validate output configuration"""
        if self.time_format not in ["iso", "human"]:
            raise ValueError("time_format must be 'iso' or 'human'")
        if self.dpi <= 0:
            raise ValueError("DPI must be positive")
        if self.chart_width <= 0 or self.chart_height <= 0:
            raise ValueError("Chart dimensions must be positive")


@dataclass
class ClientConfig:
    """Main client configuration"""
    greptime: GreptimeConnConfig
    assets: List[str]
    table_asset_prefix: str = "liqwid_supply_positions_"
    deposits_prefix: str = "liqwid_deposits_"
    withdrawals_prefix: str = "liqwid_withdrawals_"
    # New: transaction-to-timebase alignment method for corrected gains
    # allowed: 'none' (current), 'right_open' (Solution A), 'detect_spike' (Solution B)
    # plus 'snap_to_next_pos'|'snap_to_prev_pos' to align to nearest original position samples
    alignment_method: str = "none"
    # Which transaction time field to use when aligning events on the timebase
    # One of: 'timestamp' (default), 'created_at'. Future: 'auto'.
    tx_timestamp_source: str = "timestamp"
    date_range: DateRange = field(default_factory=DateRange)
    output: OutputConfig = field(default_factory=OutputConfig)
    logging_level: str = "INFO"
    cli_date_override: bool = False  # Track if dates were overridden by CLI args
    
    def __post_init__(self):
        """Validate main configuration"""
        # Normalize asset symbols to lowercase for consistency (if any provided)
        if self.assets:
            self.assets = [asset.lower().strip() for asset in self.assets]
            # Remove empty assets
            self.assets = [asset for asset in self.assets if asset]
        
        if not self.table_asset_prefix:
            raise ValueError("table_asset_prefix cannot be empty")
        if not self.deposits_prefix:
            raise ValueError("deposits_prefix cannot be empty")
        if not self.withdrawals_prefix:
            raise ValueError("withdrawals_prefix cannot be empty")
        
        # Validate logging level
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if self.logging_level.upper() not in valid_levels:
            raise ValueError(f"logging_level must be one of: {valid_levels}")
        
        self.logging_level = self.logging_level.upper()
        # Validate alignment method
        allowed_align = {"none", "right_open", "detect_spike", "snap_to_next_pos", "snap_to_prev_pos", "snap_to_nearest_pos"}
        am = (self.alignment_method or "none").strip().lower()
        if am not in allowed_align:
            raise ValueError(f"alignment_method must be one of {sorted(allowed_align)}")
        self.alignment_method = am

        # Validate tx timestamp source
        allowed_ts_src = {"timestamp", "created_at"}
        ts_src = (self.tx_timestamp_source or "timestamp").strip().lower()
        if ts_src not in allowed_ts_src:
            raise ValueError(f"tx_timestamp_source must be one of {sorted(allowed_ts_src)}")
        self.tx_timestamp_source = ts_src


class ConfigError(Exception):
    """Configuration-related errors"""
    pass


def _parse_datetime(value: Any) -> Optional[datetime]:
    """
    Parse datetime from various formats
    
    Args:
        value: String, datetime, or None
        
    Returns:
        Parsed datetime or None
        
    Raises:
        ConfigError: If datetime format is invalid
    """
    if value is None:
        return None
    
    if isinstance(value, datetime):
        return value
    
    if isinstance(value, str):
        # Try common ISO formats
        formats = [
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d"
        ]
        
        for fmt in formats:
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        
        raise ConfigError(f"Invalid datetime format: {value}. Expected ISO format like '2025-02-01T00:00:00Z'")
    
    raise ConfigError(f"Datetime must be string or datetime object, got {type(value)}")


def _load_raw_config(config_path: str) -> Dict[str, Any]:
    """
    Load raw configuration from YAML file
    
    Args:
        config_path: Path to YAML configuration file
        
    Returns:
        Raw configuration dictionary
        
    Raises:
        ConfigError: If file cannot be loaded or parsed
    """
    config_file = Path(config_path)
    
    if not config_file.exists():
        raise ConfigError(f"Configuration file not found: {config_path}")
    
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            raw_config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in {config_path}: {e}")
    except Exception as e:
        raise ConfigError(f"Failed to read {config_path}: {e}")
    
    if not isinstance(raw_config, dict):
        raise ConfigError(f"Configuration must be a YAML mapping, got {type(raw_config)}")
    
    return raw_config


def _build_config_from_dict(config_dict: Dict[str, Any]) -> ClientConfig:
    """
    Build ClientConfig from dictionary
    
    Args:
        config_dict: Raw configuration dictionary
        
    Returns:
        Validated ClientConfig instance
        
    Raises:
        ConfigError: If configuration is invalid
    """
    try:
        # Extract client section
        client_config = config_dict.get("client", {})
        if not isinstance(client_config, dict):
            raise ConfigError("'client' section must be a mapping")
        
        # Build GreptimeDB config
        greptime_raw = client_config.get("greptime", {})
        greptime_config = GreptimeConnConfig(
            host=greptime_raw.get("host", "http://localhost"),
            port=greptime_raw.get("port", 4000),
            database=greptime_raw.get("database", "liqwid"),
            timeout=greptime_raw.get("timeout", 10)
        )
        
        # Build date range config
        date_range_raw = client_config.get("date_range", {})
        date_range = DateRange(
            start=_parse_datetime(date_range_raw.get("start")),
            end=_parse_datetime(date_range_raw.get("end"))
        )
        
        # Build output config
        output_raw = client_config.get("output", {})
        
        # Build smoothing config with backward compatibility
        smoothing_raw = output_raw.get("smoothing", {})
        
        # Check if this is the new asset-specific format (has 'default' or asset keys)
        if "default" in smoothing_raw or any(key not in ["window_type", "window_size_hours", "gaussian_std", "polynomial_order"] for key in smoothing_raw.keys()):
            # New asset-specific format
            default_raw = smoothing_raw.get("default", {})
            default_method = SmoothingMethod(
                window_type=default_raw.get("window_type", "gaussian"),
                window_size_hours=default_raw.get("window_size_hours", 24.0),
                gaussian_std=default_raw.get("gaussian_std", 0.3),
                polynomial_order=default_raw.get("polynomial_order", 2)
            )
            
            # Parse asset overrides
            asset_overrides = {}
            for key, value in smoothing_raw.items():
                if key != "default" and isinstance(value, dict):
                    asset_overrides[key.lower()] = SmoothingMethod(
                        window_type=value.get("window_type", default_method.window_type),
                        window_size_hours=value.get("window_size_hours", default_method.window_size_hours),
                        gaussian_std=value.get("gaussian_std", default_method.gaussian_std),
                        polynomial_order=value.get("polynomial_order", default_method.polynomial_order)
                    )
            
            smoothing_config = AssetSmoothingConfig(
                default=default_method,
                asset_overrides=asset_overrides
            )
        else:
            # Old single smoothing format - create AssetSmoothingConfig with default only
            default_method = SmoothingMethod(
                window_type=smoothing_raw.get("window_type", "gaussian"),
                window_size_hours=smoothing_raw.get("window_size_hours", 24.0),
                gaussian_std=smoothing_raw.get("gaussian_std", 0.3),
                polynomial_order=smoothing_raw.get("polynomial_order", 2)
            )
            smoothing_config = AssetSmoothingConfig(default=default_method)
        
        output_config = OutputConfig(
            dir=output_raw.get("dir", "client/output"),
            time_format=output_raw.get("time_format", "iso"),
            dpi=output_raw.get("dpi", 300),
            chart_width=output_raw.get("chart_width", 12.0),
            chart_height=output_raw.get("chart_height", 8.0),
            include_charts=output_raw.get("include_charts", True),
            include_gains_charts=output_raw.get("include_gains_charts", False),
            smoothing=smoothing_config
        )
        
        # New optional nested transactions block (backward compatible)
        tx_raw = client_config.get("transactions", {}) if isinstance(client_config.get("transactions", {}), dict) else {}

        # Extract assets list (nested takes precedence)
        assets = tx_raw.get("assets") if tx_raw.get("assets") is not None else client_config.get("assets", [])
        if not isinstance(assets, list):
            raise ConfigError("'assets' must be a list")

        # Determine transaction timestamp source
        # Priority: explicit transactions.timestamp_source -> legacy boolean use_created_at -> client-level fallback
        ts_src = str(tx_raw.get("timestamp_source", client_config.get("timestamp_source", "timestamp"))).strip().lower()
        use_created_at_legacy = bool(tx_raw.get("use_created_at", client_config.get("use_created_at", False)))
        if ts_src not in ("timestamp", "created_at"):
            ts_src = "created_at" if use_created_at_legacy else "timestamp"
        
        # Build main config
        config = ClientConfig(
            greptime=greptime_config,
            assets=assets,
            table_asset_prefix=tx_raw.get("table_asset_prefix", client_config.get("table_asset_prefix", "liqwid_supply_positions_")),
            deposits_prefix=tx_raw.get("deposits_prefix", client_config.get("deposits_prefix", "liqwid_deposits_")),
            withdrawals_prefix=tx_raw.get("withdrawals_prefix", client_config.get("withdrawals_prefix", "liqwid_withdrawals_")),
            date_range=date_range,
            output=output_config,
            logging_level=client_config.get("logging", {}).get("level", "INFO"),
            alignment_method=str(tx_raw.get("alignment_method", client_config.get("alignment_method", "none"))),
            tx_timestamp_source=ts_src,
        )
        
        return config
        
    except (ValueError, TypeError) as e:
        raise ConfigError(f"Configuration validation failed: {e}")


def load_client_config(config_path: str) -> ClientConfig:
    """
    Load and validate client configuration from YAML file
    
    Args:
        config_path: Path to YAML configuration file
        
    Returns:
        Validated ClientConfig instance
        
    Raises:
        ConfigError: If configuration cannot be loaded or is invalid
    """
    try:
        raw_config = _load_raw_config(config_path)
        config = _build_config_from_dict(raw_config)
        
        # Ensure output directory exists
        output_dir = Path(config.output.dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        return config
        
    except ConfigError:
        raise
    except Exception as e:
        raise ConfigError(f"Unexpected error loading configuration: {e}")


def apply_cli_overrides(config: ClientConfig, **overrides) -> ClientConfig:
    """
    Apply command-line overrides to configuration
    
    Args:
        config: Base configuration
        **overrides: Override values
        
    Returns:
        Updated configuration
        
    Raises:
        ConfigError: If overrides are invalid
    """
    try:
        # Create a copy to avoid modifying original
        import copy
        updated_config = copy.deepcopy(config)
        
        # Apply overrides
        if "start" in overrides and overrides["start"]:
            updated_config.date_range.start = _parse_datetime(overrides["start"])
        
        if "end" in overrides and overrides["end"]:
            updated_config.date_range.end = _parse_datetime(overrides["end"])
        
        if "assets" in overrides and overrides["assets"]:
            if isinstance(overrides["assets"], str):
                # Parse comma-separated string
                assets = [a.strip().lower() for a in overrides["assets"].split(",")]
                updated_config.assets = [a for a in assets if a]
            elif isinstance(overrides["assets"], list):
                updated_config.assets = [str(a).strip().lower() for a in overrides["assets"]]
        
        if "output_dir" in overrides and overrides["output_dir"]:
            updated_config.output.dir = str(overrides["output_dir"])
        
        # Re-validate after overrides
        updated_config.__post_init__()
        updated_config.date_range.__post_init__()
        updated_config.output.__post_init__()
        
        return updated_config
        
    except Exception as e:
        raise ConfigError(f"Failed to apply CLI overrides: {e}")


def setup_logging(config: ClientConfig) -> None:
    """
    Setup logging based on configuration
    
    Args:
        config: Client configuration
    """
    logging.basicConfig(
        level=getattr(logging, config.logging_level),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
