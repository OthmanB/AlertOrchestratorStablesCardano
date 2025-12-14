#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prometheus-style metrics registry with minimal dependencies.

This module provides a lightweight in-process registry for gauges with optional
label sets, and a renderer for Prometheus text exposition format.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple, Optional
import threading
import time


@dataclass
class Gauge:
    name: str
    help: str
    value: float = 0.0
    labels: Optional[Tuple[Tuple[str, str], ...]] = None  # sorted tuples


class MetricsRegistry:
    def __init__(self, prefix: str = "wo_") -> None:
        self.prefix = prefix
        self._gauges: Dict[Tuple[str, Optional[Tuple[Tuple[str, str], ...]]], Gauge] = {}
        self._lock = threading.Lock()

    def _key(self, name: str, labels: Optional[Dict[str, str]]) -> Tuple[str, Optional[Tuple[Tuple[str, str], ...]]]:
        if labels:
            lab_items = tuple(sorted((str(k), str(v)) for k, v in labels.items()))
        else:
            lab_items = None
        return (name, lab_items)

    def set_gauge(self, name: str, value: float, help: str = "", labels: Optional[Dict[str, str]] = None) -> None:
        key = self._key(name, labels)
        with self._lock:
            self._gauges[key] = Gauge(name=name, help=help, value=float(value), labels=key[1])

    def get_gauge(self, name: str, labels: Optional[Dict[str, str]] = None) -> Optional[Gauge]:
        return self._gauges.get(self._key(name, labels))

    def render_prometheus(self) -> str:
        # Group by metric name for HELP/TYPE headers
        with self._lock:
            items = list(self._gauges.values())
        by_name: Dict[str, list[Gauge]] = {}
        for g in items:
            by_name.setdefault(g.name, []).append(g)

        lines: list[str] = []
        for name, group in sorted(by_name.items()):
            full_name = f"{self.prefix}{name}" if not name.startswith(self.prefix) else name
            # HELP/TYPE once per metric
            help_text = group[0].help or name
            lines.append(f"# HELP {full_name} {help_text}")
            lines.append(f"# TYPE {full_name} gauge")
            for g in group:
                label_str = ""
                if g.labels:
                    pairs = ",[".join([f'{k}="{v}"' for k, v in g.labels])
                    label_str = f"{{{pairs}}}"
                lines.append(f"{full_name}{label_str} {g.value}")
        return "\n".join(lines) + "\n"

    def set_timestamp_now(self, metric_name: str = "last_eval_timestamp_seconds") -> None:
        self.set_gauge(metric_name, time.time(), help="Last evaluation time (UTC epoch)")
