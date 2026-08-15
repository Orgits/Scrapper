"""
Standalone runner for local testing — no Celery/Redis needed. Handy while
you're still tuning selectors for a new target site.

Usage:
    python scripts/run_once.py example_site
"""
import sys
import asyncio
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from scraper.tasks import _load_targets, _scrape_target  # noqa: E402

if __name__ == "__main__":
    target_name = sys.argv[1] if len(sys.argv) > 1 else "example_site"
    targets = _load_targets()
    target = next((t for t in targets if t["name"] == target_name), None)
    if target is None:
        available = [t["name"] for t in targets]
        raise ValueError(
            f"Unknown target '{target_name}'. Available targets: {available}"
        )
    asyncio.run(_scrape_target(target))
