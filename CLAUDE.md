# Fitness Assistant — Developer Context

## What this is

A personal AI fitness trainer built around Claude Desktop + MCP. The system
ingests data from multiple fitness apps, stores it locally in SQLite, and
exposes it to Claude Desktop via an MCP server. Claude acts as a fitness
trainer and nutritionist, using real data to give personalised advice and
weekly plans.

The user does **not** write code — all changes go through Claude Code.

## Why it exists

The user tracks fitness across four apps (Strava, Garmin/Apple Health,
MacroFactor, Strong) but each app works in isolation and none of them can
give joined-up advice. The key problems being solved:

1. **TDEE estimation bias** — MacroFactor underestimates TDEE because missed
   logging days (where the user typically eats more than on logged days) look
   like a calorie surplus, pushing the estimate down. We apply a gap correction
   based on weight trend vs. expected weight change.

2. **Activity-based fuelling** — A flat daily calorie target ignores the
   difference between a heavy training day and a rest day. The system gives
   separate targets per day type.

3. **Long-term context** — LLMs have no memory across sessions. We persist
   goals, check-in summaries, and user profile notes in SQLite so Claude always
   has continuity.

4. **Gap-filling via conversation** — Claude can ask about unlogged days,
   missed sessions, or anomalies in the data to build a fuller picture.

## Architecture

```
Claude Desktop
    │  MCP protocol (stdio)
    ▼
src/mcp/server.py          — MCP server, all tools defined here
    │
    ├── src/database.py    — SQLite connection + schema init
    ├── src/tools/tdee.py  — TDEE gap-correction logic
    └── src/ingestion/
            strava.py      — OAuth + activity sync
            macrofactor.py — CSV importer
            strong.py      — CSV importer
            apple_health.py — XML importer
```

**Entry points:**
- `run_mcp.py` — called by Claude Desktop to start the MCP server
- `strava_auth.py` — run once to authorise Strava (OAuth flow)
- `import_data.py` — drop files in data/imports/, run to import
- `setup.bat` — first-time setup: venv, deps, DB init, Claude Desktop config

**Credentials:** stored in `.env` (gitignored). Strava OAuth tokens stored
in `config/strava_tokens.json` (also gitignored). Nothing is ever sent to
Anthropic servers except the query results.

## Data sources

| Source | Format | Cadence | Import method |
|--------|--------|---------|---------------|
| Strava | API | On demand / manual sync | `sync_strava()` MCP tool |
| MacroFactor | CSV export | Manual, ~weekly | Drop in data/imports/ |
| Strong | CSV export | Manual, after sessions | Drop in data/imports/ |
| Apple Health | XML export | Manual, ~weekly | Drop in data/imports/ |
| Garmin | CSV export | Alternative to Apple Health | Drop in data/imports/ |

## Database

SQLite at `db/fitness.db`. Tables:

- `activities` — Strava activities
- `nutrition_logs` — MacroFactor daily logs (calories, macros, MF TDEE estimate)
- `weight_logs` — weight entries (from MacroFactor or Apple Health)
- `workouts` + `workout_sets` — Strong gym sessions
- `daily_health` — Apple Health metrics (steps, sleep, HRV, resting HR)
- `goals` — fitness goals with status (active/achieved/abandoned)
- `checkins` — end-of-session summaries and weekly plans
- `tdee_estimates` — our adjusted TDEE calculations
- `user_profile` — key-value store for persistent context notes

## MCP tools exposed to Claude Desktop

- `get_recent_activities(days)`
- `get_nutrition_summary(days)`
- `get_weight_trend(days)`
- `get_workout_log(days)`
- `get_health_metrics(days)`
- `get_tdee_analysis_tool(days)`
- `get_user_profile()`
- `update_user_profile(key, value)`
- `get_current_goal()`
- `set_goal(title, description, target_date)`
- `complete_goal(goal_title, notes)`
- `save_checkin_summary(summary, plan_for_week, tdee_used)`
- `get_checkin_history(limit)`
- `sync_strava(days_back)`

## Development notes

- Python 3.14, dependencies minimal: `mcp`, `requests`, `python-dotenv`
- The venv lives at `.venv/` — always use `.venv/Scripts/python.exe` to run scripts
- No async code — FastMCP supports sync functions, keeps things simple
- MacroFactor and Strong CSV parsers auto-detect column names (case-insensitive)
  to handle format variations across app versions
- The system prompt for the trainer persona lives at `config/system_prompt.md`
  and is loaded by the MCP server's `@mcp.prompt()` decorator
- `config/fitness_background.md` — free-form narrative fitness history written by the
  user; loaded via `get_fitness_background()` MCP tool each session

## Planned (V2)

- Weekly planner UI: user inputs anticipated activity levels per day; Claude
  generates an exercise + nutrition plan with a simple visual card layout
- Possibly a local web app for iOS access (FastAPI + HTML, Claude API)
