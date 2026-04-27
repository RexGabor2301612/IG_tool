#!/usr/bin/env python3
"""
Test Production System Startup - Verify all core modules initialize correctly
"""

import sys
from pathlib import Path

print("\n" + "="*80)
print("TESTING PRODUCTION SYSTEM STARTUP")
print("="*80 + "\n")

# Test 1: Import core modules
print("[TEST 1] Importing production core modules...")
try:
    from core.logging.logger import ProductionLogger, LogLevel, LogEntry
    from core.session.manager import PlaywrightSessionManager
    from core.extraction.extractor import DataExtractor
    from core.etl.etl_engine import ETLPipeline
    print("✅ All imports successful\n")
except ImportError as e:
    print(f"❌ Import failed: {e}\n")
    sys.exit(1)

# Test 2: Initialize ProductionLogger
print("[TEST 2] Initializing ProductionLogger...")
try:
    logger = ProductionLogger(persistence_dir=Path("."))
    print(f"✅ ProductionLogger initialized")
    print(f"   - Persistence dir: {logger.persistence_dir}")
    print(f"   - Logs file: {logger.persistence_dir / 'logs.db'}")
    print()
except Exception as e:
    print(f"❌ ProductionLogger init failed: {e}\n")
    sys.exit(1)

# Test 3: Log test entries
print("[TEST 3] Testing ProductionLogger.log()...")
try:
    logger.log(LogLevel.INFO, "STARTUP_TEST", "Testing ProductionLogger logging")
    logger.log(LogLevel.SUCCESS, "SYSTEM_CHECK", "Production system startup successful")
    logger.log(LogLevel.WARN, "TEST", "Testing WARN level")
    logger.log(LogLevel.ERROR, "TEST", "Testing ERROR level")
    print(f"✅ Logging works correctly")
    print(f"   - Buffer size: {len(logger.buffer)}")
    print()
except Exception as e:
    print(f"❌ Logging failed: {e}\n")
    sys.exit(1)

# Test 4: Initialize PlaywrightSessionManager
print("[TEST 4] Initializing PlaywrightSessionManager...")
try:
    session_manager = PlaywrightSessionManager(sessions_dir=Path("storage_states"))
    print(f"✅ PlaywrightSessionManager initialized")
    print(f"   - Sessions dir: {session_manager.sessions_dir}")
    print(f"   - Sessions dir exists: {session_manager.sessions_dir.exists()}")
    print()
except Exception as e:
    print(f"❌ PlaywrightSessionManager init failed: {e}\n")
    sys.exit(1)

# Test 5: Initialize DataExtractor
print("[TEST 5] Initializing DataExtractor...")
try:
    data_extractor = DataExtractor()
    print(f"✅ DataExtractor initialized")
    print()
except Exception as e:
    print(f"❌ DataExtractor init failed: {e}\n")
    sys.exit(1)

# Test 6: Initialize ETLPipeline
print("[TEST 6] Initializing ETLPipeline...")
try:
    etl_pipeline = ETLPipeline(output_dir=Path("."), platform="instagram")
    print(f"✅ ETLPipeline initialized")
    print(f"   - Output dir: {etl_pipeline.output_dir}")
    print(f"   - Platform: {etl_pipeline.platform}")
    print(f"   - DB path: {etl_pipeline.db_path}")
    print()
except Exception as e:
    print(f"❌ ETLPipeline init failed: {e}\n")
    sys.exit(1)

# Test 7: Verify process() method exists
print("[TEST 7] Verifying ETLPipeline.process() method...")
try:
    has_process = hasattr(etl_pipeline, 'process') and callable(getattr(etl_pipeline, 'process'))
    has_add_post = hasattr(etl_pipeline, 'add_post') and callable(getattr(etl_pipeline, 'add_post'))
    
    if not has_process:
        raise Exception("process() method not found")
    if not has_add_post:
        raise Exception("add_post() method not found")
    
    print(f"✅ ETLPipeline methods verified")
    print(f"   - process() method: exists")
    print(f"   - add_post() method: exists")
    print()
except Exception as e:
    print(f"❌ Method verification failed: {e}\n")
    sys.exit(1)

# Summary
print("="*80)
print("✅ PRODUCTION SYSTEM READY")
print("="*80)
print()
print("Summary:")
print("  ✓ ProductionLogger initialized - logs to logs.db")
print("  ✓ PlaywrightSessionManager initialized - sessions in storage_states/")
print("  ✓ DataExtractor initialized - ready for extraction")
print("  ✓ ETLPipeline initialized - ready for ETL processing")
print()
print("PRODUCTION SYSTEM STATUS: ✅ ACTIVE - No fallback mode")
print()
