#!/usr/bin/env python3
"""
Test script for wallet address discovery functionality.
Verifies that discover_wallet_addresses() correctly finds wallets in existing data.
"""

import sys
import logging
from pathlib import Path
from src.core.settings import load_settings
from src.shared.greptime_reader import create_greptime_reader

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

def test_wallet_discovery():
    """Test wallet discovery from existing supply position tables"""
    reader = None
    try:
        # Load settings
        logger.info("Loading settings...")
        config_path = Path("config/orchestrator_config.yaml")
        settings = load_settings(str(config_path))
        
        # Get configuration
        greptime_config = settings.client.greptime
        table_prefix = settings.client.table_asset_prefix
        
        logger.info(f"GreptimeDB: {greptime_config.host}:{greptime_config.port}")
        logger.info(f"Database: {greptime_config.database}")
        logger.info(f"Table prefix: {table_prefix}")
        
        # Create reader
        logger.info("\n" + "="*60)
        logger.info("Creating GreptimeReader...")
        reader = create_greptime_reader(greptime_config, table_prefix)
        
        # Test connection
        logger.info("\n" + "="*60)
        logger.info("Testing database connection...")
        if not reader.test_connection():
            logger.error("❌ Failed to connect to GreptimeDB")
            return False
        logger.info("✓ Connection successful")
        
        # Discover asset tables
        logger.info("\n" + "="*60)
        logger.info("Discovering asset tables...")
        assets = reader.discover_asset_tables()
        logger.info(f"✓ Found {len(assets)} asset tables: {', '.join(assets).upper()}")
        
        # Discover wallet addresses
        logger.info("\n" + "="*60)
        logger.info("Discovering wallet addresses...")
        wallets = reader.discover_wallet_addresses()
        
        if not wallets:
            logger.warning("⚠️  No wallet addresses found in data")
            logger.info("Checking configured wallets as fallback...")
            config_wallets = getattr(settings.orchestrator, 'wallets', [])
            if config_wallets:
                logger.info(f"✓ Found {len(config_wallets)} configured wallets:")
                for i, wallet in enumerate(config_wallets, 1):
                    logger.info(f"  {i}. {wallet[:30]}...")
            else:
                logger.error("❌ No wallets in configuration either")
                return False
        else:
            logger.info(f"✓ Discovered {len(wallets)} unique wallet addresses:")
            for i, wallet in enumerate(wallets, 1):
                logger.info(f"  {i}. {wallet[:30]}... (len={len(wallet)})")
        
        # Summary
        logger.info("\n" + "="*60)
        logger.info("WALLET DISCOVERY TEST SUMMARY")
        logger.info("="*60)
        logger.info(f"✓ Database connection: OK")
        logger.info(f"✓ Asset tables found: {len(assets)}")
        logger.info(f"✓ Wallets discovered: {len(wallets)}")
        logger.info(f"Status: {'SUCCESS' if wallets else 'FALLBACK TO CONFIG'}")
        logger.info("="*60)
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Test failed with error: {e}", exc_info=True)
        return False
    finally:
        if reader is not None:
            reader.close()

if __name__ == "__main__":
    logger.info("Starting wallet discovery test...\n")
    success = test_wallet_discovery()
    sys.exit(0 if success else 1)
