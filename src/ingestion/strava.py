"""
Strava ingestion — OAuth authorisation flow and activity sync.

First-time use:  run strava_auth.py  (opens browser, saves tokens)
Ongoing:         call sync_activities() — refreshes token automatically
"""

import json
import os
import time
import threading
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs

import requests
from dotenv import load_dotenv

load_dotenv()

TOKENS_FILE = Path(__file__).parent.parent.parent / "config" / "strava_tokens.json"
REDIRECT_URI = "http://localhost:8000/callback"
AUTH_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"


# ---------------------------------------------------------------------------
# Token persistence
# ---------------------------------------------------------------------------

def load_tokens() -> dict | None:
    if TOKENS_FILE.exists():
        return json.loads(TOKENS_FILE.read_text())
    return None


def save_tokens(tokens: dict):
    TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKENS_FILE.write_text(json.dumps(tokens, indent=2))


def ensure_valid_token() -> str:
    """Return a valid access token, refreshing if necessary."""
    tokens = load_tokens()
    if tokens is None:
        raise RuntimeError(
            "No Strava tokens found. Run strava_auth.py first to authorise."
        )

    expires_at = tokens.get("expires_at", 0)
    if time.time() < expires_at - 60:
        return tokens["access_token"]

    # Refresh
    client_id = os.getenv("STRAVA_CLIENT_ID")
    client_secret = os.getenv("STRAVA_CLIENT_SECRET")
    resp = requests.post(TOKEN_URL, data={
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
    }, timeout=10)
    resp.raise_for_status()
    new_tokens = resp.json()
    save_tokens(new_tokens)
    return new_tokens["access_token"]


# ---------------------------------------------------------------------------
# OAuth authorisation (run once)
# ---------------------------------------------------------------------------

_auth_code: str | None = None


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _auth_code
        params = parse_qs(urlparse(self.path).query)
        if "code" in params:
            _auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"""
                <html><body style="font-family:sans-serif;padding:40px">
                <h2>Authorised!</h2>
                <p>You can close this tab and return to the terminal.</p>
                </body></html>
            """)
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Missing code parameter.")

    def log_message(self, *args):
        pass  # suppress request logging


def authorise():
    """
    Run the OAuth flow.  Opens a browser tab; catches the callback locally.
    Saves tokens to config/strava_tokens.json.
    """
    global _auth_code
    _auth_code = None

    client_id = os.getenv("STRAVA_CLIENT_ID")
    if not client_id:
        raise RuntimeError("STRAVA_CLIENT_ID not set in .env")

    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "approval_prompt": "auto",
        "scope": "activity:read_all",
    }
    url = AUTH_URL + "?" + urlencode(params)

    server = HTTPServer(("localhost", 8000), _CallbackHandler)

    def serve():
        server.handle_request()

    t = threading.Thread(target=serve, daemon=True)
    t.start()

    print("Opening Strava authorisation in your browser...")
    print("If it doesn't open automatically, visit:\n", url)
    webbrowser.open(url)

    t.join(timeout=120)
    server.server_close()

    if _auth_code is None:
        raise RuntimeError("Did not receive authorisation code within 2 minutes.")

    client_secret = os.getenv("STRAVA_CLIENT_SECRET")
    resp = requests.post(TOKEN_URL, data={
        "client_id": client_id,
        "client_secret": client_secret,
        "code": _auth_code,
        "grant_type": "authorization_code",
    }, timeout=10)
    resp.raise_for_status()
    tokens = resp.json()
    save_tokens(tokens)
    print("Strava authorisation complete. Tokens saved.")
    return tokens


# ---------------------------------------------------------------------------
# Activity sync
# ---------------------------------------------------------------------------

def sync_activities(days_back: int = 90) -> int:
    """
    Fetch activities from Strava and upsert into the database.
    Returns the number of new activities inserted.
    """
    from src.database import get_connection
    import json as _json

    access_token = ensure_valid_token()
    headers = {"Authorization": f"Bearer {access_token}"}

    after_ts = int(time.time()) - (days_back * 86400)
    page = 1
    inserted = 0

    conn = get_connection()

    while True:
        resp = requests.get(ACTIVITIES_URL, headers=headers, params={
            "after": after_ts,
            "per_page": 200,
            "page": page,
        }, timeout=15)
        resp.raise_for_status()
        activities = resp.json()

        if not activities:
            break

        for a in activities:
            conn.execute("""
                INSERT INTO activities (
                    strava_id, name, sport_type, start_date,
                    distance_meters, moving_time_seconds, elapsed_time_seconds,
                    elevation_gain, average_heartrate, max_heartrate,
                    average_watts, kilojoules, calories, suffer_score,
                    description, raw_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(strava_id) DO UPDATE SET
                    name=excluded.name,
                    sport_type=excluded.sport_type,
                    start_date=excluded.start_date,
                    distance_meters=excluded.distance_meters,
                    moving_time_seconds=excluded.moving_time_seconds,
                    elapsed_time_seconds=excluded.elapsed_time_seconds,
                    elevation_gain=excluded.elevation_gain,
                    average_heartrate=excluded.average_heartrate,
                    max_heartrate=excluded.max_heartrate,
                    average_watts=excluded.average_watts,
                    kilojoules=excluded.kilojoules,
                    calories=excluded.calories,
                    suffer_score=excluded.suffer_score,
                    description=excluded.description,
                    raw_json=excluded.raw_json
            """, (
                a.get("id"),
                a.get("name"),
                a.get("sport_type") or a.get("type"),
                a.get("start_date"),
                a.get("distance"),
                a.get("moving_time"),
                a.get("elapsed_time"),
                a.get("total_elevation_gain"),
                a.get("average_heartrate"),
                a.get("max_heartrate"),
                a.get("average_watts"),
                a.get("kilojoules"),
                a.get("calories"),
                a.get("suffer_score"),
                a.get("description"),
                _json.dumps(a),
            ))
            if conn.execute(
                "SELECT changes()"
            ).fetchone()[0]:
                inserted += 1

        conn.commit()
        page += 1

    conn.close()
    return inserted
