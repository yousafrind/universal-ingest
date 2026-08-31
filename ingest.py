#!/usr/bin/env python3
"""Thin entrypoint — the real implementation lives in ingest_tool/. See README.md.

    python ingest.py dir <source> <output>
    python ingest.py web <url> <output>
    python ingest.py papers "<query>" <output>
    python ingest.py --selftest
"""

from ingest_tool.cli import main

if __name__ == "__main__":
    main()
