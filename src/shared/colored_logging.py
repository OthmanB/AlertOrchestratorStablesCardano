#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Colored logging formatter for better console readability.

Adds ANSI color codes to log levels:
- DEBUG: Cyan
- INFO: Green
- WARNING: Yellow
- ERROR: Red
- CRITICAL: Bold Red
"""
from __future__ import annotations

import logging
import sys
from typing import Optional


class ColoredFormatter(logging.Formatter):
    """Custom formatter that adds colors to log levels."""
    
    # ANSI color codes
    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[1;31m', # Bold Red
    }
    RESET = '\033[0m'
    
    def __init__(self, fmt: str = '%(asctime)s - %(levelname)s - [%(name)s:%(module)s.%(funcName)s:%(lineno)d] - %(message)s', datefmt: Optional[str] = None, use_colors: bool = True):
        """
        Initialize the colored formatter.
        
        Args:
            fmt: Format string for log messages
            datefmt: Format string for timestamps
            use_colors: Whether to use colors (auto-disabled if not a TTY)
        """
        super().__init__(fmt, datefmt)
        # Auto-detect if we should use colors (only if outputting to a terminal)
        self.use_colors = use_colors and hasattr(sys.stderr, 'isatty') and sys.stderr.isatty()
    
    def format(self, record: logging.LogRecord) -> str:
        """Format the log record with colors."""
        if self.use_colors and record.levelname in self.COLORS:
            # Save original levelname
            orig_levelname = record.levelname
            # Add color to levelname
            record.levelname = f"{self.COLORS[record.levelname]}{record.levelname}{self.RESET}"
            # Format the record
            result = super().format(record)
            # Restore original levelname
            record.levelname = orig_levelname
            return result
        else:
            return super().format(record)


def setup_colored_logging(level: int = logging.INFO, fmt: str = '%(asctime)s - %(levelname)s - [%(name)s:%(module)s.%(funcName)s:%(lineno)d] - %(message)s', datefmt: Optional[str] = None) -> None:
    """
    Configure the root logger with colored output.
    
    Args:
        level: Logging level (e.g., logging.INFO, logging.DEBUG)
        fmt: Format string for log messages
        datefmt: Format string for timestamps
    """
    # Remove existing handlers to avoid duplicates
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
    
    # Create console handler with colored formatter
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(ColoredFormatter(fmt=fmt, datefmt=datefmt))
    
    # Configure root logger
    root.setLevel(level)
    root.addHandler(console_handler)
