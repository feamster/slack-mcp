#!/usr/bin/env python3
"""Entry point for Slack MCP server."""

import sys
from pathlib import Path

# Add the project root to path
sys.path.insert(0, str(Path(__file__).parent))

from src.mcp_server import main

if __name__ == "__main__":
    main()
