#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit tests for WalletBreakdown model
"""

import pytest
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from shared.models import WalletBreakdown


def test_wallet_breakdown_creation():
    """Test basic WalletBreakdown creation"""
    wb = WalletBreakdown(
        wallet_address="addr1qxytz12345678901234567890abcdef5ur5m",
        wmax_usd=1000.0
    )
    assert wb.wallet_address == "addr1qxytz12345678901234567890abcdef5ur5m"
    assert wb.wmax_usd == 1000.0


def test_abbreviated_address():
    """Test address abbreviation for long addresses"""
    long_addr = "addr1qxytz12345678901234567890abcdef5ur5m"
    wb = WalletBreakdown(wallet_address=long_addr, wmax_usd=500.0)
    
    abbreviated = wb.abbreviated_address()
    
    # Should be: first 11 chars + "..." + last 6 chars
    # addr1qxytz1 (11 chars) + ... + f5ur5m (last 6)
    assert abbreviated == "addr1qxytz1...f5ur5m"
    assert len(abbreviated) == 20  # 11 + 3 + 6


def test_abbreviated_address_short():
    """Test that short addresses are not abbreviated"""
    short_addr = "addr1q123456"
    wb = WalletBreakdown(wallet_address=short_addr, wmax_usd=250.0)
    
    abbreviated = wb.abbreviated_address()
    
    # Should return full address since it's <= 17 chars
    assert abbreviated == short_addr


def test_wallet_breakdown_sum():
    """Test summing multiple wallet breakdowns"""
    breakdowns = [
        WalletBreakdown("wallet1_addr", 500.0),
        WalletBreakdown("wallet2_addr", 300.0),
        WalletBreakdown("wallet3_addr", 200.0),
    ]
    
    total = sum(wb.wmax_usd for wb in breakdowns)
    
    assert total == 1000.0


def test_wallet_breakdown_validation_empty_address():
    """Test that empty wallet address raises ValueError"""
    with pytest.raises(ValueError, match="wallet_address cannot be empty"):
        WalletBreakdown(wallet_address="", wmax_usd=100.0)
    
    with pytest.raises(ValueError, match="wallet_address cannot be empty"):
        WalletBreakdown(wallet_address="   ", wmax_usd=100.0)


def test_wallet_breakdown_validation_negative_wmax():
    """Test that negative wmax_usd raises ValueError"""
    with pytest.raises(ValueError, match="wmax_usd cannot be negative"):
        WalletBreakdown(wallet_address="addr1q123", wmax_usd=-100.0)


def test_empty_list_sum():
    """Test summing empty wallet breakdown list"""
    breakdowns = []
    total = sum(wb.wmax_usd for wb in breakdowns)
    assert total == 0.0


def test_real_cardano_address():
    """Test with realistic Cardano address"""
    # Example Cardano stake address format
    real_addr = "addr1qxytzp3kvgcx4upmwz5jvjvxqza7pu3j3jl9qgqz5kxgqgqgqgqgqgqgqgqgqgqgq5ur5m"
    wb = WalletBreakdown(wallet_address=real_addr, wmax_usd=5000.0)
    
    abbreviated = wb.abbreviated_address()
    
    # Check format
    assert abbreviated.startswith("addr1qxytzp")
    assert abbreviated.endswith("5ur5m")
    assert "..." in abbreviated
    assert len(abbreviated) == 20


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
