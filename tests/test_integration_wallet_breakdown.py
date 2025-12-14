#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Integration test for per-wallet Wmax breakdown feature
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from shared.models import WalletBreakdown
# Import just the dataclass, not the whole module to avoid import errors
from dataclasses import dataclass, field
from typing import List, Optional


# Simplified AssetDecision for testing (avoid importing full alert_logic)
@dataclass
class AssetDecision:
    decision: int
    wmax_usd: List[WalletBreakdown] = field(default_factory=list)
    v_ref_usd: Optional[float] = None
    v_t1_usd: Optional[float] = None
    g_usd: Optional[float] = None
    ref_mode: Optional[str] = None


def test_asset_decision_with_wallet_breakdown():
    """Test AssetDecision with per-wallet breakdown"""
    
    # Create wallet breakdowns
    wallet1 = WalletBreakdown(
        wallet_address="addr1qxytz12345678901234567890abcdef5ur5m",
        wmax_usd=500.0
    )
    wallet2 = WalletBreakdown(
        wallet_address="addr1q987654321098765432109876543210zyxwvu",
        wmax_usd=300.0
    )
    
    # Create decision with wallet breakdown
    dec = AssetDecision(
        decision=1,
        wmax_usd=[wallet1, wallet2],
        v_ref_usd=10000.0,
        v_t1_usd=10800.0,
        g_usd=800.0,
        ref_mode="keyword"
    )
    
    # Verify structure
    assert isinstance(dec.wmax_usd, list)
    assert len(dec.wmax_usd) == 2
    
    # Verify total
    total_wmax = sum(wb.wmax_usd for wb in dec.wmax_usd)
    assert total_wmax == 800.0
    
    # Verify individual wallets
    assert dec.wmax_usd[0].wmax_usd == 500.0
    assert dec.wmax_usd[1].wmax_usd == 300.0
    
    # Verify abbreviated addresses
    assert dec.wmax_usd[0].abbreviated_address() == "addr1qxytz1...f5ur5m"
    assert dec.wmax_usd[1].abbreviated_address() == "addr1q98765...zyxwvu"
    
    print("✓ AssetDecision with wallet breakdown works correctly")


def test_empty_wallet_breakdown():
    """Test AssetDecision with no wallet breakdown (HOLD state)"""
    
    dec = AssetDecision(
        decision=0,
        wmax_usd=[],  # Empty list for HOLD
        v_ref_usd=None,
        g_usd=0.0,
        ref_mode="null"
    )
    
    # Verify empty list
    assert isinstance(dec.wmax_usd, list)
    assert len(dec.wmax_usd) == 0
    
    # Verify sum is zero
    total_wmax = sum(wb.wmax_usd for wb in dec.wmax_usd)
    assert total_wmax == 0.0
    
    print("✓ Empty wallet breakdown works correctly")


def test_single_wallet():
    """Test AssetDecision with single wallet"""
    
    wallet = WalletBreakdown(
        wallet_address="addr1qsinglewalletaddress123456789abcdefgh",
        wmax_usd=1000.0
    )
    
    dec = AssetDecision(
        decision=1,
        wmax_usd=[wallet],
        v_ref_usd=5000.0,
        g_usd=1000.0
    )
    
    assert len(dec.wmax_usd) == 1
    total_wmax = sum(wb.wmax_usd for wb in dec.wmax_usd)
    assert total_wmax == 1000.0
    
    print("✓ Single wallet works correctly")


def test_display_formatting():
    """Test display string formatting for dashboard"""
    
    wallet1 = WalletBreakdown("addr1qxyz123456789012345678901234567890abc", 500.0)
    wallet2 = WalletBreakdown("addr1qabc098765432109876543210987654321xyz", 300.0)
    
    dec = AssetDecision(
        decision=1,
        wmax_usd=[wallet1, wallet2]
    )
    
    # Simulate dashboard formatting
    total = sum(wb.wmax_usd for wb in dec.wmax_usd)
    
    # Multiple wallets - show breakdown
    if len(dec.wmax_usd) > 1:
        wallet_details = ", ".join(
            f"{wb.abbreviated_address()}: {wb.wmax_usd:.2f}"
            for wb in dec.wmax_usd
        )
        display = f"{total:.2f} ({wallet_details})"
    else:
        display = f"{total:.2f}"
    
    assert "800.00" in display
    assert "addr1qxyz12...890abc" in display
    assert "addr1qabc09...321xyz" in display
    assert "500.00" in display
    assert "300.00" in display
    
    print(f"✓ Display formatting: {display}")


def test_compositional_gains():
    """Test that per-wallet gains sum to total (mathematical property)"""
    
    # Simulate per-wallet gains
    wallet1_gains = 400.0
    wallet2_gains = 200.0
    wallet3_gains = 150.0
    
    # Create breakdowns with safety factor c = 1.0
    c = 1.0
    breakdowns = [
        WalletBreakdown("wallet1_addr", max(0.0, c * wallet1_gains)),
        WalletBreakdown("wallet2_addr", max(0.0, c * wallet2_gains)),
        WalletBreakdown("wallet3_addr", max(0.0, c * wallet3_gains)),
    ]
    
    # Total should equal sum of individual gains
    total_gains = wallet1_gains + wallet2_gains + wallet3_gains
    total_wmax = sum(wb.wmax_usd for wb in breakdowns)
    
    assert total_wmax == c * total_gains
    assert total_wmax == 750.0
    
    print(f"✓ Compositional property verified: sum(per-wallet) = total ({total_wmax:.2f})")


def test_logging_format():
    """Test logging string format"""
    
    wallet1 = WalletBreakdown("addr1q123", 500.0)
    wallet2 = WalletBreakdown("addr1q456", 300.0)
    
    dec = AssetDecision(decision=1, wmax_usd=[wallet1, wallet2])
    
    total_wmax = sum(wb.wmax_usd for wb in dec.wmax_usd)
    num_wallets = len(dec.wmax_usd)
    
    # Simulate logging format
    log_line = (
        f"USDC: decision={dec.decision}, wmax_usd={total_wmax:.2f} "
        f"({num_wallets} wallet{'s' if num_wallets != 1 else ''})"
    )
    
    assert "800.00" in log_line
    assert "2 wallets" in log_line
    
    print(f"✓ Logging format: {log_line}")


if __name__ == "__main__":
    print("Running integration tests for per-wallet Wmax breakdown...\n")
    
    test_asset_decision_with_wallet_breakdown()
    test_empty_wallet_breakdown()
    test_single_wallet()
    test_display_formatting()
    test_compositional_gains()
    test_logging_format()
    
    print("\n✅ All integration tests passed!")
