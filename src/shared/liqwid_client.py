#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Liqwid & Koios API clients for fetching market data and wallet assets.
Implements retry logic, error handling, and response parsing.
"""

import requests
import time
import json
from datetime import datetime, UTC, timedelta
from typing import List, Dict, Optional, Any, Tuple
from dataclasses import dataclass

from .models import Market, PricePoint, WalletAsset
from .logging_setup import get_logger

logger = get_logger(__name__)

@dataclass
class APIResponse:
    """Generic API response wrapper"""
    success: bool
    data: Any = None
    error: Optional[str] = None
    status_code: Optional[int] = None

class LiqwidClient:
    """Client for Liqwid GraphQL API"""
    
    def __init__(self, endpoint: str, timeout: int = 30, retry_attempts: int = 3, retry_backoff: int = 5):
        """
        Initialize Liqwid client
        
        Args:
            endpoint: GraphQL endpoint URL
            timeout: Request timeout in seconds
            retry_attempts: Number of retry attempts
            retry_backoff: Base backoff time between retries
        """
        self.endpoint = endpoint
        self.timeout = timeout
        self.retry_attempts = retry_attempts
        self.retry_backoff = retry_backoff
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': 'LiqwidQueryDaemon/1.0'
        })
    
    def _make_request(self, query: str, variables: Optional[Dict] = None) -> APIResponse:
        """
        Make GraphQL request with retry logic
        
        Args:
            query: GraphQL query string
            variables: Query variables
            
        Returns:
            APIResponse with success status and data/error
        """
        payload = {
            'query': query,
            'variables': variables or {}
        }
        
        last_error = None
        
        for attempt in range(self.retry_attempts):
            try:
                logger.debug(f"Making GraphQL request (attempt {attempt + 1}/{self.retry_attempts})")
                logger.debug(f"Endpoint: {self.endpoint}")
                logger.debug(f"Timeout: {self.timeout}s")
                
                # Use direct requests.post instead of session to avoid any connection pooling issues
                response = requests.post(
                    self.endpoint,
                    json=payload,
                    headers={
                        'Content-Type': 'application/json',
                        'Accept': 'application/json'
                    },
                    timeout=self.timeout
                )
                
                if response.status_code == 200:
                    data = response.json()
                    
                    # Check for GraphQL errors
                    if 'errors' in data:
                        error_msg = '; '.join([err.get('message', 'Unknown GraphQL error') for err in data['errors']])
                        logger.error(f"GraphQL errors: {error_msg}")
                        return APIResponse(success=False, error=error_msg, status_code=200)
                    
                    logger.debug("GraphQL request successful")
                    return APIResponse(success=True, data=data.get('data'), status_code=200)
                
                else:
                    error_msg = f"HTTP {response.status_code}: {response.text}"
                    logger.warning(f"Request failed: {error_msg}")
                    last_error = error_msg
                    
            except requests.exceptions.Timeout:
                last_error = f"Request timeout after {self.timeout}s"
                logger.warning(f"Attempt {attempt + 1} timed out")
                
            except requests.exceptions.ConnectionError as e:
                last_error = f"Connection error: {str(e)}"
                logger.warning(f"Attempt {attempt + 1} connection failed")
                
            except Exception as e:
                last_error = f"Unexpected error: {str(e)}"
                logger.error(f"Attempt {attempt + 1} failed unexpectedly: {e}")
            
            # Exponential backoff before retry (except on last attempt)
            if attempt < self.retry_attempts - 1:
                backoff_time = self.retry_backoff * (2 ** attempt)
                logger.debug(f"Retrying in {backoff_time}s...")
                time.sleep(backoff_time)
        
        logger.error(f"All {self.retry_attempts} attempts failed. Last error: {last_error}")
        return APIResponse(success=False, error=last_error)
    
    def fetch_markets(self) -> List[Market]:
        """
        Fetch all Liqwid supply markets
        
        Returns:
            List of Market objects
            
        Raises:
            Exception: If markets cannot be fetched after all retries
        """
        logger.info("Fetching Liqwid markets...")
        
        query = """
        query GetAllMarkets {
          liqwid {
            data {
              markets(input: { perPage: 100, page: 0 }) {
                results {
                  id
                  displayName
                  symbol
                  exchangeRate
                  asset {
                    id
                    displayName
                    symbol
                    decimals
                    currencySymbol
                    policyId
                    price
                  }
                  receiptAsset {
                    id
                    displayName
                    symbol
                    decimals
                    currencySymbol
                    policyId
                  }
                }
              }
            }
          }
        }
        """
        
        response = self._make_request(query)
        
        if not response.success:
            raise Exception(f"Failed to fetch markets: {response.error}")
        
        markets_data = response.data.get('liqwid', {}).get('data', {}).get('markets', {}).get('results', [])
        if not markets_data:
            logger.warning("No markets returned from GraphQL query")
            return []
        
        markets = []
        for market_data in markets_data:
            try:
                market = Market(
                    id=market_data['id'],
                    name=market_data['displayName'],
                    underlying_symbol=market_data['asset']['symbol'],
                    underlying_decimals=int(market_data['asset']['decimals']),
                    underlying_price=market_data['asset'].get('price'),  # May be None
                    # Normalize policy ID to lowercase for consistent matching
                    qtoken_policy=market_data['receiptAsset']['policyId'].lower() if market_data['receiptAsset'].get('policyId') else market_data['receiptAsset']['policyId'],
                    qtoken_symbol=market_data['receiptAsset']['symbol'],
                    qtoken_decimals=int(market_data['receiptAsset']['decimals']),
                    exchange_rate=float(market_data['exchangeRate'])
                )
                markets.append(market)
                
            except (KeyError, ValueError, TypeError) as e:
                logger.error(f"Failed to parse market data: {e}")
                logger.debug(f"Market data: {market_data}")
                continue
        
        logger.info(f"Successfully fetched {len(markets)} markets")
        return markets
    
    def fetch_asset_prices(self, symbols: List[str]) -> Dict[str, PricePoint]:
        """
        Fetch current prices for asset symbols
        
        Args:
            symbols: List of asset symbols to fetch prices for
            
        Returns:
            Dictionary mapping symbol to PricePoint
        """
        if not symbols:
            return {}
        
        logger.info(f"Fetching prices for {len(symbols)} assets: {symbols}")
        
        # Use Liqwid assets query to get prices
        query = """
        query GetAssetPrices {
          liqwid {
            data {
              assets(input: { perPage: 100, page: 0 }) {
                results {
                  symbol
                  price
                }
              }
            }
          }
        }
        """
        
        response = self._make_request(query)
        
        if not response.success:
            logger.error(f"Failed to fetch prices: {response.error}")
            return {}
        
        assets_data = response.data.get('liqwid', {}).get('data', {}).get('assets', {}).get('results', [])
        prices = {}
        timestamp = datetime.now(UTC)
        
        for asset_data in assets_data:
            try:
                symbol = asset_data['symbol']
                price = asset_data.get('price')
                
                # Filter to requested symbols and only if price exists
                if symbol in symbols and price is not None:
                    prices[symbol] = PricePoint(
                        symbol=symbol,
                        price=float(price),
                        timestamp=timestamp
                    )
                    
            except (KeyError, ValueError, TypeError) as e:
                logger.error(f"Failed to parse price data for {asset_data}: {e}")
                continue
        
        logger.info(f"Successfully fetched prices for {len(prices)} assets")
        return prices
    
    def fetch_historical_transactions(
        self,
        wallet_address: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Fetch historical transactions (deposits/withdrawals) for a wallet address.
        
        Args:
            wallet_address: Cardano wallet address (addr1...)
            start_date: Optional start date in ISO format (defaults to 1 year ago)
            end_date: Optional end date in ISO format (defaults to now)
        
        Returns:
            Dictionary with:
                - status: 'success' or 'error'
                - transactions: List of transaction dicts (if success)
                - total_count: Number of transactions (if success)
                - error: Error message (if error)
        
        Transaction format:
            {
                'id': str,              # Transaction hash
                'type': str,            # 'SUPPLY' (deposit) or 'WITHDRAW' (withdrawal)
                'displayName': str,     # Asset name (e.g., 'wanUSDT', 'DJED')
                'time': str,            # ISO timestamp
                'amount': float         # Transaction amount
            }
        
        Notes:
            - API returns 100 results per page by default (not configurable via input)
            - Date filter is REQUIRED for API to return results
            - SUPPLY type maps to deposits, WITHDRAW type maps to withdrawals
            - Pagination fields are in response but not request
        """
        logger.info(f"Fetching historical transactions for wallet {wallet_address[:20]}...")
        
        # Set default date range if not provided (all available API history)
        # Liqwid V2 API historical data starts ~November 2023
        # Use October 1, 2023 as safe earliest date to capture all available data
        if not start_date:
            start = datetime(2023, 10, 1, tzinfo=UTC)
            start_date = start.replace(tzinfo=None).isoformat() + 'Z'
        elif not start_date.endswith('Z'):
            # Convert YYYY-MM-DD to ISO timestamp with Z suffix
            start_date = f"{start_date}T00:00:00Z"
        
        if not end_date:
            end_date = datetime.now(UTC).replace(tzinfo=None).isoformat() + 'Z'
        elif not end_date.endswith('Z'):
            # Convert YYYY-MM-DD to ISO timestamp with Z suffix
            end_date = f"{end_date}T23:59:59Z"
        
        logger.info(f"Date range: {start_date} to {end_date}")
        
        # GraphQL query - must include pagination fields for API to work properly
        query = """
        query Transactions($input: HistoricalTransactionInput) {
          historical {
            transactions(input: $input) {
              page
              perPage
              pagesCount
              totalCount
              results {
                id
                type
                displayName
                time
                amount
                logo
                amountUSD
                principal
                principalUSD
                oraclePrice
                exchangeRate
                qAmount
                minInterest
                healthFactor
                loanOriginationFee
                beforeHealthFactor
                beforePrincipal
                beforePrincipalUSD
                totalCollateralUSD
                beforetotalCollateralUSD
              }
            }
          }
        }
        """
        
        # Variables - date filter is required for results to populate
        # NOTE: page and perPage are NOT part of the input object!
        # They are returned in the response but not sent in the request
        input_data = {
            "addresses": [wallet_address],
            "date": {
                "startTime": start_date,
                "endTime": end_date
            }
        }
        
        logger.info(f"Date filter: {start_date} to {end_date}")
        
        variables = {"input": input_data}
        
        # Debug: Log the actual request
        logger.debug(f"GraphQL variables: {json.dumps(variables, indent=2)}")
        
        response = self._make_request(query, variables)
        
        # Debug: Log full response
        if response.success:
            logger.debug(f"Full API response data: {json.dumps(response.data, indent=2)}")
        
        if not response.success:
            logger.error(f"Failed to fetch transactions: {response.error}")
            return {
                'status': 'error',
                'error': response.error,
                'transactions': [],
                'total_count': 0
            }
        
        # Parse response
        try:
            historical_data = response.data.get('historical', {})
            transactions_data = historical_data.get('transactions', {})
            
            # Debug: Log the full structure
            logger.debug(f"Response structure - historical: {historical_data is not None}")
            logger.debug(f"Response structure - transactions keys: {list(transactions_data.keys())[:5]}")
            logger.debug(f"Full transactions_data: {transactions_data}")
            
            results = transactions_data.get('results', [])
            total_count = transactions_data.get('totalCount', 0)
            
            logger.info(f"Successfully fetched {len(results)} transactions (totalCount: {total_count})")
            
            # Log transaction type breakdown
            supply_count = sum(1 for tx in results if tx.get('type') == 'SUPPLY')
            withdraw_count = sum(1 for tx in results if tx.get('type') == 'WITHDRAW')
            logger.debug(f"Transaction breakdown: {supply_count} SUPPLY (deposits), "
                        f"{withdraw_count} WITHDRAW (withdrawals)")
            
            return {
                'status': 'success',
                'transactions': results,
                'total_count': total_count,
                'wallet_address': wallet_address,
                'date_range': {
                    'start': start_date,
                    'end': end_date
                }
            }
            
        except Exception as e:
            logger.error(f"Failed to parse transaction response: {e}")
            return {
                'status': 'error',
                'error': f"Failed to parse response: {str(e)}",
                'transactions': [],
                'total_count': 0
            }

class KoiosClient:
    """Client for Koios API to fetch wallet assets"""
    
    def __init__(self, endpoint: str, timeout: int = 30, retry_attempts: int = 3, retry_backoff: int = 5):
        """
        Initialize Koios client
        
        Args:
            endpoint: Koios API endpoint URL
            timeout: Request timeout in seconds
            retry_attempts: Number of retry attempts
            retry_backoff: Base backoff time between retries
        """
        self.endpoint = endpoint.rstrip('/')
        self.timeout = timeout
        self.retry_attempts = retry_attempts
        self.retry_backoff = retry_backoff
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'LiqwidQueryDaemon/1.0'
        })
    
    def _make_request(self, path: str, method: str = 'GET', data: Optional[Any] = None) -> APIResponse:
        """
        Make HTTP request with retry logic
        
        Args:
            path: API path (without base URL)
            method: HTTP method
            data: Request data for POST requests
            
        Returns:
            APIResponse with success status and data/error
        """
        url = f"{self.endpoint}/{path.lstrip('/')}"
        last_error = None
        
        for attempt in range(self.retry_attempts):
            try:
                logger.debug(f"Making Koios {method} request to {path} (attempt {attempt + 1}/{self.retry_attempts})")
                
                if method == 'GET':
                    response = self.session.get(url, timeout=self.timeout)
                elif method == 'POST':
                    response = self.session.post(url, json=data, timeout=self.timeout)
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")
                
                if response.status_code == 200:
                    try:
                        data = response.json()
                        logger.debug(f"Koios request successful, got {len(data) if isinstance(data, list) else 1} item(s)")
                        return APIResponse(success=True, data=data, status_code=200)
                    except json.JSONDecodeError as e:
                        error_msg = f"Invalid JSON response: {e}"
                        logger.error(error_msg)
                        return APIResponse(success=False, error=error_msg, status_code=200)
                
                else:
                    error_msg = f"HTTP {response.status_code}: {response.text}"
                    logger.warning(f"Koios request failed: {error_msg}")
                    last_error = error_msg
                    
            except requests.exceptions.Timeout:
                last_error = f"Request timeout after {self.timeout}s"
                logger.warning(f"Attempt {attempt + 1} timed out")
                
            except requests.exceptions.ConnectionError as e:
                last_error = f"Connection error: {str(e)}"
                logger.warning(f"Attempt {attempt + 1} connection failed")
                
            except Exception as e:
                last_error = f"Unexpected error: {str(e)}"
                logger.error(f"Attempt {attempt + 1} failed unexpectedly: {e}")
            
            # Exponential backoff before retry (except on last attempt)
            if attempt < self.retry_attempts - 1:
                backoff_time = self.retry_backoff * (2 ** attempt)
                logger.debug(f"Retrying in {backoff_time}s...")
                time.sleep(backoff_time)
        
        logger.error(f"All {self.retry_attempts} attempts failed. Last error: {last_error}")
        return APIResponse(success=False, error=last_error)
    
    def fetch_wallet_assets(self, wallet_address: str) -> List[WalletAsset]:
        """
        Fetch all assets for a wallet address
        
        Args:
            wallet_address: Cardano wallet address
            
        Returns:
            List of WalletAsset objects
            
        Raises:
            Exception: If wallet assets cannot be fetched after all retries
        """
        logger.info(f"Fetching assets for wallet: {wallet_address[:20]}...")
        
        # Use Koios address_assets endpoint with correct format
        response = self._make_request(
            'address_assets',
            method='POST',
            data={"_addresses": [wallet_address]}
        )

        if not response.success:
            raise Exception(f"Failed to fetch wallet assets: {response.error}")

        raw_payload = response.data
        if not raw_payload:
            logger.info(f"No assets found for wallet {wallet_address[:20]}...")
            return []

        # Determine response shape
        assets_raw: List[dict] = []
        try:
            first = raw_payload[0] if isinstance(raw_payload, list) and raw_payload else None
        except Exception:
            first = None

        shape = 'unknown'
        if first and isinstance(first, dict):
            if 'asset_list' in first:  # Address wrapped shape
                shape = 'address_wrapped'
                logger.debug('Detected Koios response shape: address_wrapped')
                # Find the matching address entry (prefer exact address); fallback to first with asset_list
                target_entry = None
                for entry in raw_payload:
                    if isinstance(entry, dict) and entry.get('address') == wallet_address and entry.get('asset_list'):
                        target_entry = entry
                        break
                    if not target_entry and isinstance(entry, dict) and entry.get('asset_list'):
                        target_entry = entry
                assets_raw = target_entry.get('asset_list', []) if target_entry else []
            elif 'policy_id' in first:  # Flat asset list shape
                shape = 'flat_asset_list'
                logger.debug('Detected Koios response shape: flat_asset_list')
                assets_raw = raw_payload
            else:
                logger.warning(f"Unrecognized Koios asset response keys: {list(first.keys())[:5]}")
        else:
            logger.warning("Unrecognized Koios asset response structure (non-dict first element)")

        assets: List[WalletAsset] = []
        for asset_data in assets_raw:
            try:
                policy_id = asset_data['policy_id'].lower()
                quantity_raw = asset_data['quantity']
                # Koios sometimes returns quantity as string
                quantity_int = int(quantity_raw)
                decimals_val = asset_data.get('decimals', 0) or 0
                asset = WalletAsset(
                    policy_id=policy_id,
                    asset_name=asset_data.get('asset_name', ''),
                    fingerprint=asset_data.get('fingerprint', ''),
                    decimals=decimals_val,
                    quantity=quantity_int
                )
                assets.append(asset)
            except (KeyError, ValueError, TypeError) as e:
                logger.error(f"Failed to parse asset data: {e}")
                logger.debug(f"Asset data: {asset_data}")
                continue

        if raw_payload and not assets:
            logger.warning(f"Parsed 0 assets from Koios payload size {len(raw_payload)} (shape={shape}) for wallet {wallet_address[:20]}...")

        logger.info(f"Found {len(assets)} assets for wallet {wallet_address[:20]}...")
        return assets
    
    def fetch_asset_metadata(self, policy_id: str, asset_name: str = '') -> Optional[Dict[str, Any]]:
        """
        Fetch metadata for a specific asset
        
        Args:
            policy_id: Asset policy ID
            asset_name: Asset name (optional)
            
        Returns:
            Asset metadata dictionary or None if not found
        """
        logger.debug(f"Fetching metadata for asset {policy_id}{asset_name}")
        
        # Use asset_info endpoint
        asset_identifier = policy_id + asset_name if asset_name else policy_id
        
        response = self._make_request(
            'asset_info',
            method='POST',
            data=[asset_identifier]
        )
        
        if not response.success:
            logger.warning(f"Failed to fetch asset metadata: {response.error}")
            return None
        
        metadata_list = response.data
        if not metadata_list:
            return None
        
        # Return first matching asset metadata
        return metadata_list[0] if metadata_list else None

class APIClientManager:
    """Manager class that coordinates Liqwid and Koios clients"""
    
    def __init__(self, liqwid_endpoint: str, koios_endpoint: str, 
                 timeout: int = 30, retry_attempts: int = 3, retry_backoff: int = 5):
        """
        Initialize API client manager
        
        Args:
            liqwid_endpoint: Liqwid GraphQL endpoint
            koios_endpoint: Koios API endpoint
            timeout: Request timeout
            retry_attempts: Number of retry attempts
            retry_backoff: Base backoff time
        """
        self.liqwid = LiqwidClient(liqwid_endpoint, timeout, retry_attempts, retry_backoff)
        self.koios = KoiosClient(koios_endpoint, timeout, retry_attempts, retry_backoff)
        self.logger = get_logger(__name__)
    
    def test_connections(self) -> Tuple[bool, bool]:
        """
        Test connectivity to both APIs
        
        Returns:
            Tuple of (liqwid_ok, koios_ok)
        """
        self.logger.info("Testing API connections...")
        
        # Test Liqwid with simple query
        liqwid_ok = False
        try:
            markets = self.liqwid.fetch_markets()
            liqwid_ok = len(markets) >= 0  # Even empty response is OK
            self.logger.info(f"Liqwid connection: {'OK' if liqwid_ok else 'FAILED'}")
        except Exception as e:
            self.logger.error(f"Liqwid connection failed: {e}")
        
        # Test Koios with info endpoint (no wallet needed)
        koios_ok = False
        try:
            response = self.koios._make_request('tip')
            koios_ok = response.success
            self.logger.info(f"Koios connection: {'OK' if koios_ok else 'FAILED'}")
        except Exception as e:
            self.logger.error(f"Koios connection failed: {e}")
        
        return liqwid_ok, koios_ok
