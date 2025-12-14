#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Token registry loader for Minswap policyId + token name hex mappings.

CSV format expected at config/token_registry.csv:
asset,policy_id,token_name_hex
usdm,48cbb3...,0014df10...
...

Notes:
- Lines starting with '#' are ignored.
- Header row is required.
- Duplicate assets overwrite earlier entries (last wins).
- Multiple assets may share a policy_id (e.g., bridged tokens); reverse lookup returns a list.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import csv


@dataclass(frozen=True)
class TokenRecord:
    asset: str
    policy_id: str
    token_name_hex: str

    @property
    def token_id_hex(self) -> str:
        """Concatenated token_id used by many APIs (policy id + token name hex)."""
        return f"{self.policy_id}{self.token_name_hex}"


class TokenRegistryError(Exception):
    pass


class TokenRegistry:
    def __init__(self, records: List[TokenRecord]) -> None:
        self._by_asset: Dict[str, TokenRecord] = {}
        self._by_policy: Dict[str, List[TokenRecord]] = {}
        for rec in records:
            a = rec.asset.strip().lower()
            self._by_asset[a] = rec
            self._by_policy.setdefault(rec.policy_id, []).append(rec)

    def get_by_asset(self, asset: str) -> Optional[TokenRecord]:
        if not asset:
            return None
        return self._by_asset.get(asset.strip().lower())

    def get_assets_by_policy(self, policy_id: str) -> List[TokenRecord]:
        if not policy_id:
            return []
        return list(self._by_policy.get(policy_id.strip().lower(), []))

    def validate_assets_present(self, assets: List[str]) -> Tuple[bool, List[str]]:
        missing = []
        for a in assets or []:
            if self.get_by_asset(a) is None:
                missing.append(a)
        return (len(missing) == 0, missing)


def load_registry(csv_path: str | Path) -> TokenRegistry:
    p = Path(csv_path)
    if not p.exists():
        raise TokenRegistryError(f"Token registry file not found: {p}")

    records: List[TokenRecord] = []
    with p.open("r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header_seen = False
        for row in reader:
            if not row:
                continue
            if row[0].strip().startswith("#"):
                continue
            if not header_seen:
                header_seen = True
                # minimal header check
                if len(row) < 3:
                    raise TokenRegistryError("Invalid registry header: expected 3 columns: asset,policy_id,token_name_hex")
                continue
            if len(row) < 3:
                continue
            asset = row[0].strip()
            policy_id = row[1].strip().lower()
            token_hex = row[2].strip().lower()
            if not asset or not policy_id or not token_hex:
                continue
            # basic hex validation (policy ids are 56 hex chars on Cardano; token names vary)
            def _is_hex(s: str) -> bool:
                try:
                    int(s, 16)
                    return True
                except Exception:
                    return False
            if not _is_hex(policy_id) or not _is_hex(token_hex):
                continue
            records.append(TokenRecord(asset=asset, policy_id=policy_id, token_name_hex=token_hex))
    if not records:
        raise TokenRegistryError("Token registry is empty after parsing")
    return TokenRegistry(records)
