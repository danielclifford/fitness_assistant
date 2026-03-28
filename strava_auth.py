"""
Run this ONCE to authorise Strava access.
It will open your browser, ask you to approve access, then save your tokens.
After this you never need to run it again (tokens refresh automatically).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.ingestion.strava import authorise

if __name__ == "__main__":
    print("=" * 50)
    print("  Strava Authorisation")
    print("=" * 50)
    print()
    try:
        tokens = authorise()
        athlete = tokens.get("athlete", {})
        name = f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip()
        print(f"\nConnected to Strava as: {name or 'unknown athlete'}")
        print("All done — you can close this window.")
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)
