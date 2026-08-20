#!/usr/bin/env python3
"""
Standalone script to process all CSV files in companydata/ and scrape tofler.in.

Usage:
    python scripts/process_companydata.py
"""
import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from scraper.csv_processor import main  # noqa: E402

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())