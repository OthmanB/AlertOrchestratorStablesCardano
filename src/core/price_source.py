#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Price sources for Phase C: compare Liqwid vs Minswap prices.

Exposes a minimal PriceSource interface and two implementations:
- MinswapAggregatorPriceSource: uses the public Aggregator API
- LiqwidGraphQLPriceSource: uses shared Liqwid GraphQL client

Both return the latest USD price for a given display asset name, leveraging
the on-disk token registry for reliable mapping.
"""
from __future__ import annotations

from typing import Optional, Dict
import time
import logging

import requests

from .token_registry import TokenRegistry
from ..shared.liqwid_client import LiqwidClient


class PriceSource:
    def get_latest_price_usd(self, asset: str) -> Optional[float]:
        raise NotImplementedError


class MinswapAggregatorPriceSource(PriceSource):
    def __init__(self, *, base_url: str, currency: str = "usd", timeout_s: int = 5, retries: int = 1, registry: TokenRegistry) -> None:
        self.base_url = base_url.rstrip("/")
        self.currency = currency
        self.timeout_s = max(1, int(timeout_s))
        self.retries = max(0, int(retries))
        self.registry = registry
        self.log = logging.getLogger(__name__)
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "WO/price-compare"})

    def _get(self, path: str, params: Optional[Dict[str, str]] = None) -> Optional[Dict]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        last_err: Optional[str] = None
        self.log.info(f"Minswap GET path={path} params={params}")
        for i in range(self.retries + 1):
            try:
                r = self._session.get(url, params=params, timeout=self.timeout_s)
                if r.status_code == 200:
                    try:
                        data = r.json()
                    except Exception:
                        data = None
                    self.log.info(f"Minswap GET OK path={path}")
                    return data
                last_err = f"HTTP {r.status_code}: {r.text[:200]}"
            except Exception as e:
                last_err = str(e)
            if i < self.retries:
                time.sleep(0.5 * (2 ** i))
        self.log.warning(f"Minswap GET FAILED path={path} error={last_err}")
        return None

    def _post(self, path: str, json_body: Dict) -> Optional[Dict]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        last_err: Optional[str] = None
        self.log.info(f"Minswap POST path={path} body_keys={list(json_body.keys()) if isinstance(json_body, dict) else 'n/a'}")
        for i in range(self.retries + 1):
            try:
                r = self._session.post(url, json=json_body, timeout=self.timeout_s)
                if r.status_code == 200:
                    if r.headers.get("Content-Type", "").startswith("application/json"):
                        self.log.info(f"Minswap POST OK path={path}")
                        return r.json()
                    else:
                        last_err = f"Unexpected content-type: {r.headers.get('Content-Type')[:64]}"
                        break
                last_err = f"HTTP {r.status_code}: {r.text[:200]}"
            except Exception as e:
                last_err = str(e)
            if i < self.retries:
                time.sleep(0.5 * (2 ** i))
        self.log.warning(f"Minswap POST FAILED path={path} error={last_err}")
        return None

    def get_latest_price_usd(self, asset: str) -> Optional[float]:
        self.log.info(f"Minswap: fetching USD price asset={asset}")
        # Map asset to token_id via registry (policy + token hex)
        rec = self.registry.get_by_asset(asset)
        if rec is None:
            self.log.warning(f"Minswap: registry lookup failed asset={asset}")
            return None
        token_id_hex = rec.token_id_hex
        # 1) ADA/USD
        ada = self._get("aggregator/ada-price", params={"currency": self.currency})
        if not ada or "value" not in ada or "price" not in ada["value"]:
            self.log.warning("Minswap: ADA/USD fetch failed")
            return None
        ada_usd = float(ada["value"]["price"])
        # 2) token price by ADA (requires 'query' field per API spec)
        resp = self._post(
            "aggregator/tokens",
            json_body={
                "query": "",              # empty search; filter strictly by assets
                "only_verified": False,    # include unverified tokens if needed
                "assets": [token_id_hex],
            },
        )
        if not resp or "tokens" not in resp:
            self.log.warning("Minswap: tokens fetch failed or malformed response")
            return None
        items = resp["tokens"] or []
        # Find our token entry
        token = None
        for it in items:
            try:
                if str(it.get("token_id", "")).lower() == token_id_hex.lower():
                    token = it
                    break
            except Exception:
                continue
        if not token:
            self.log.warning(f"Minswap: token not found token_id={token_id_hex}")
            return None
        p_by_ada = token.get("price_by_ada")
        if p_by_ada is None:
            self.log.warning("Minswap: price_by_ada missing in token response")
            return None
        try:
            price_usd = float(p_by_ada) * float(ada_usd)
            self.log.info(f"Minswap: price computed asset={asset} usd={price_usd:.6g}")
            return price_usd
        except Exception:
            self.log.warning("Minswap: failed to compute USD price from price_by_ada")
            return None


class LiqwidGraphQLPriceSource(PriceSource):
    def __init__(self, *, endpoint: str, timeout_s: int = 10) -> None:
        self.endpoint = endpoint
        self.timeout_s = timeout_s
        self._client = LiqwidClient(endpoint=endpoint, timeout=timeout_s, retry_attempts=2, retry_backoff=2)

    def get_latest_price_usd(self, asset: str) -> Optional[float]:
        """
        Use markets query and match by policy id when possible. As a fallback,
        try assets list by symbol (upper-cased display name). Also attempt
        bridged variants (e.g., WANUSDC/WANUSDT) when a direct symbol has no price.
        Note: This function requires the caller to ensure the registry mapping
        if policy-based matching is desired.
        """
        log = logging.getLogger(__name__)
        target = (asset or "").strip().upper()
        # Candidate symbols for Liqwid side
        candidates = [target]
        if target in {"USDC", "USDT"}:
            candidates.append(f"WAN{target}")
        # 1) Markets lookup first
        try:
            markets = self._client.fetch_markets()
        except Exception:
            markets = []
        try:
            for m in markets:
                sym = str(getattr(m, "underlying_symbol", "")).upper()
                price = getattr(m, "underlying_price", None)
                if price is None:
                    continue
                if sym in candidates:
                    if sym != target:
                        log.info(f"Liqwid: using bridged symbol {sym} for asset={target}")
                    return float(price)
        except Exception:
            pass
        # 2) Fallback to assets() for multiple candidates
        try:
            prices = self._client.fetch_asset_prices(symbols=candidates)
            for sym in candidates:
                pt = prices.get(sym)
                if pt and getattr(pt, "price", None) is not None:
                    if sym != target:
                        log.info(f"Liqwid: using bridged symbol {sym} for asset={target}")
                    return float(pt.price)
        except Exception:
            return None
        return None

