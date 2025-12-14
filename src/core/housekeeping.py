#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Housekeeping utilities for output directories.

Deletes generated artifacts older than a configured cutoff date to keep the working
tree lean and git fast. Intended to be called at startup, before the periodic loop.
"""
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import logging
from typing import Iterable

from .settings import OrchestratorConfig


def _iter_files(paths: Iterable[str], root: Path) -> Iterable[Path]:
    for p in paths:
        try:
            base = (root / p).resolve()
        except Exception:
            continue
        if not base.exists():
            continue
        if base.is_file():
            yield base
        else:
            for fp in base.rglob('*'):
                if fp.is_file():
                    yield fp


def _should_delete(path: Path, cutoff_utc: datetime, exts: set[str]) -> bool:
    try:
        if exts and path.suffix.lower() not in exts:
            return False
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        return mtime < cutoff_utc
    except Exception:
        return False


def cleanup_outputs(config: OrchestratorConfig, repo_root: str | Path) -> int:
    """Delete files matching config.cleanup older than expire_before.

    Returns number of files deleted.
    """
    log = logging.getLogger(__name__)
    cl = getattr(config, 'cleanup', None)
    if not cl or not getattr(cl, 'enabled', False):
        return 0
    cutoff = getattr(cl, 'expire_before', None)
    if not isinstance(cutoff, datetime):
        log.warning("Cleanup enabled but no valid expire_before configured; skipping.")
        return 0
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    exts = {str(e).lower() for e in (getattr(cl, 'extensions', []) or [])}
    root = Path(repo_root).resolve()
    deleted = 0
    for f in _iter_files(getattr(cl, 'paths', []) or [], root):
        try:
            if _should_delete(f, cutoff, exts):
                f.unlink(missing_ok=True)
                deleted += 1
        except Exception as e:
            log.debug(f"Cleanup: failed to delete {f}: {e}")
            continue
    if deleted > 0:
        log.info(f"Cleanup: deleted {deleted} files older than {cutoff.isoformat()} from configured output paths.")
    else:
        log.info(f"Cleanup: no files older than {cutoff.isoformat()} to delete.")
    return deleted
