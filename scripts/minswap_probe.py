#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Quick probe for Minswap Aggregator API.

Usage:
  python alert_orchestrator/scripts/minswap_probe.py --asset usdc --currency usd --base https://agg-api.minswap.org

This reads token_registry.csv to resolve the token_id and queries:
  - GET  /aggregator/ada-price
  - POST /aggregator/tokens  (with required 'query' field)
Then computes USD price = price_by_ada * ada_usd
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import requests
import csv


def load_token_id(asset: str, registry_path: Path) -> str:
    with registry_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row:
                continue
            a = (row.get("asset") or "").strip().lower()
            if a == asset.strip().lower():
                policy = (row.get("policy_id") or "").strip().lower()
                name_hex = (row.get("token_name_hex") or "").strip().lower()
                if policy and name_hex:
                    return policy + name_hex
    raise SystemExit(f"asset '{asset}' not found in registry: {registry_path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", default="usdc")
    parser.add_argument("--currency", default="usd")
    parser.add_argument("--base", default="https://agg-api.minswap.org")
    args = parser.parse_args()

    base = args.base.rstrip("/")
    script_dir = Path(__file__).resolve().parent
    registry_path = script_dir.parent / "config" / "token_registry.csv"

    print(f"Using registry: {registry_path}")
    token_id_hex = load_token_id(args.asset, registry_path)
    print(f"Resolved {args.asset} -> token_id={token_id_hex}")

    s = requests.Session()
    s.headers.update({"User-Agent": "WO/minswap-probe"})

    # 1) ADA price
    url_ada = f"{base}/aggregator/ada-price"
    print(f"GET {url_ada}?currency={args.currency}")
    r1 = s.get(url_ada, params={"currency": args.currency}, timeout=10)
    try:
        data1 = r1.json()
    except Exception:
        data1 = None
    print(f"status={r1.status_code} body={json.dumps(data1) if isinstance(data1, (dict, list)) else r1.text[:200]}")
    if r1.status_code != 200 or not isinstance(data1, dict) or "value" not in data1 or "price" not in data1["value"]:
        print("FAIL: cannot get ADA price")
        return 1
    ada_usd = float(data1["value"]["price"])
    print(f"ADA/USD={ada_usd}")

    # 2) Token price by ADA
    url_tokens = f"{base}/aggregator/tokens"
    body = {"query": "", "only_verified": False, "assets": [token_id_hex]}
    print(f"POST {url_tokens} body={body}")
    r2 = s.post(url_tokens, json=body, timeout=15)
    try:
        data2 = r2.json()
    except Exception:
        data2 = None
    print(f"status={r2.status_code} body={json.dumps(data2) if isinstance(data2, (dict, list)) else r2.text[:200]}")
    if r2.status_code != 200 or not isinstance(data2, dict) or "tokens" not in data2:
        print("FAIL: tokens query failed")
        return 2

    items = data2.get("tokens") or []
    token = None
    for it in items:
        if str(it.get("token_id", "")).lower() == token_id_hex.lower():
            token = it
            break
    if not token:
        print("FAIL: token not found in response")
        return 3

    p_by_ada = token.get("price_by_ada")
    if p_by_ada is None:
        print("FAIL: price_by_ada missing in token response")
        return 4

    usd = float(p_by_ada) * ada_usd
    print(f"SUCCESS: asset={args.asset} price_by_ada={p_by_ada} ADA/USD={ada_usd} -> USD={usd}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
