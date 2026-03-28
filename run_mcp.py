"""Entry point called by Claude Desktop to start the MCP server."""
import sys
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent))

from src.mcp.server import run

if __name__ == "__main__":
    run()
