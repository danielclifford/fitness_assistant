"""
MCP server — exposes fitness data as tools that Claude Desktop can call.

Claude uses these tools to read your data during a conversation.
It never sees your credentials; only the results of each query.
"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

# Ensure the project root is on the path so imports work
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from mcp.server.fastmcp import FastMCP
from src.database import get_connection, initialize
from src.tools.tdee import get_tdee_analysis

mcp = FastMCP("Fitness Assistant")


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _fmt_date_range(days: int) -> tuple[str, str]:
    end = date.today()
    start = end - timedelta(days=days)
    return start.isoformat(), end.isoformat()


# ---------------------------------------------------------------------------
# Tool: Strava activities
# ---------------------------------------------------------------------------

@mcp.tool()
def get_recent_activities(days: int = 14) -> str:
    """
    Get Strava activities from the last N days (default 14).
    Returns a formatted list of runs, rides, swims, hikes, etc.
    with distance, duration, heart rate, and calories.
    """
    start, end = _fmt_date_range(days)
    conn = get_connection()
    rows = conn.execute("""
        SELECT name, sport_type, start_date,
               distance_meters, moving_time_seconds,
               elevation_gain, average_heartrate, max_heartrate,
               calories, suffer_score
        FROM activities
        WHERE date(start_date) BETWEEN ? AND ?
        ORDER BY start_date DESC
    """, (start, end)).fetchall()
    conn.close()

    if not rows:
        return f"No Strava activities recorded in the last {days} days."

    lines = [f"## Strava Activities — last {days} days\n"]
    for r in rows:
        dist_km = f"{r['distance_meters']/1000:.1f} km" if r["distance_meters"] else "–"
        mins = r["moving_time_seconds"] // 60 if r["moving_time_seconds"] else None
        duration = f"{mins // 60}h {mins % 60}m" if mins and mins >= 60 else (f"{mins}m" if mins else "–")
        hr = f"{r['average_heartrate']:.0f} bpm avg" if r["average_heartrate"] else ""
        cal = f"{r['calories']:.0f} kcal" if r["calories"] else ""
        elev = f"↑{r['elevation_gain']:.0f}m" if r["elevation_gain"] else ""
        date_str = r["start_date"][:10]
        lines.append(
            f"**{date_str}** {r['sport_type']} — {r['name']}\n"
            f"  {dist_km}  {duration}  {hr}  {cal}  {elev}"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: Nutrition summary
# ---------------------------------------------------------------------------

@mcp.tool()
def get_nutrition_summary(days: int = 14) -> str:
    """
    Get nutrition data from MacroFactor for the last N days (default 14).
    Shows calories, macros, and flags incomplete/missing logging days.
    """
    start, end = _fmt_date_range(days)
    conn = get_connection()
    rows = conn.execute("""
        SELECT date, calories, protein_g, carbs_g, fat_g,
               fiber_g, macrofactor_tdee, is_complete_log
        FROM nutrition_logs
        WHERE date BETWEEN ? AND ?
        ORDER BY date DESC
    """, (start, end)).fetchall()
    conn.close()

    # Identify days with no log at all
    logged_dates = {r["date"] for r in rows}
    all_dates = []
    d = date.today()
    for _ in range(days):
        all_dates.append(d.isoformat())
        d -= timedelta(days=1)

    missing = [d for d in all_dates if d not in logged_dates]

    lines = [f"## Nutrition Log — last {days} days\n"]

    for r in rows:
        flag = "" if r["is_complete_log"] else " ⚠️ incomplete"
        cal = f"{r['calories']:.0f}" if r["calories"] else "–"
        p   = f"P:{r['protein_g']:.0f}g" if r["protein_g"] else ""
        c   = f"C:{r['carbs_g']:.0f}g"   if r["carbs_g"]   else ""
        f_  = f"F:{r['fat_g']:.0f}g"     if r["fat_g"]     else ""
        mf  = f"(MF TDEE: {r['macrofactor_tdee']:.0f})" if r["macrofactor_tdee"] else ""
        lines.append(f"**{r['date']}**{flag}  {cal} kcal  {p} {c} {f_}  {mf}")

    if missing:
        lines.append(f"\n⚠️ **No log at all** on {len(missing)} day(s): {', '.join(missing[:7])}")
        if len(missing) > 7:
            lines.append(f"  (and {len(missing)-7} more)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: Weight trend
# ---------------------------------------------------------------------------

@mcp.tool()
def get_weight_trend(days: int = 30) -> str:
    """
    Get weight measurements and trend over the last N days (default 30).
    Shows individual readings and the overall change.
    """
    start, end = _fmt_date_range(days)
    conn = get_connection()
    rows = conn.execute("""
        SELECT date, weight_kg, source
        FROM weight_logs
        WHERE date BETWEEN ? AND ?
        ORDER BY date ASC
    """, (start, end)).fetchall()
    conn.close()

    if not rows:
        return f"No weight data in the last {days} days."

    change = rows[-1]["weight_kg"] - rows[0]["weight_kg"]
    direction = "▲" if change > 0 else "▼" if change < 0 else "—"
    avg = sum(r["weight_kg"] for r in rows) / len(rows)

    lines = [
        f"## Weight — last {days} days\n",
        f"Start: {rows[0]['weight_kg']:.1f} kg  |  "
        f"Latest: {rows[-1]['weight_kg']:.1f} kg  |  "
        f"Change: {direction}{abs(change):.1f} kg  |  "
        f"Average: {avg:.1f} kg\n",
    ]
    for r in rows:
        lines.append(f"  {r['date']}  {r['weight_kg']:.1f} kg  ({r['source']})")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: Gym workouts
# ---------------------------------------------------------------------------

@mcp.tool()
def get_workout_log(days: int = 14) -> str:
    """
    Get gym sessions logged in the Strong app for the last N days (default 14).
    Shows exercises, sets, weights, and reps.
    """
    start, end = _fmt_date_range(days)
    conn = get_connection()
    workouts = conn.execute("""
        SELECT id, date, name, duration_minutes
        FROM workouts
        WHERE date BETWEEN ? AND ?
        ORDER BY date DESC
    """, (start, end)).fetchall()

    if not workouts:
        conn.close()
        return f"No gym sessions logged in the last {days} days."

    lines = [f"## Gym Sessions — last {days} days\n"]
    for w in workouts:
        dur = f"{w['duration_minutes']}min" if w["duration_minutes"] else ""
        lines.append(f"**{w['date']}** — {w['name']} {dur}")
        sets = conn.execute("""
            SELECT exercise_name, set_number, weight_kg, reps, rpe
            FROM workout_sets
            WHERE workout_id = ?
            ORDER BY exercise_name, set_number
        """, (w["id"],)).fetchall()

        current_ex = None
        ex_lines = []
        for s in sets:
            if s["exercise_name"] != current_ex:
                if ex_lines:
                    lines.append(f"  *{current_ex}*: " + "  ".join(ex_lines))
                current_ex = s["exercise_name"]
                ex_lines = []
            wt = f"{s['weight_kg']:.1f}kg" if s["weight_kg"] else "BW"
            rpe = f" @RPE{s['rpe']:.0f}" if s["rpe"] else ""
            ex_lines.append(f"{s['reps']}×{wt}{rpe}")
        if ex_lines and current_ex:
            lines.append(f"  *{current_ex}*: " + "  ".join(ex_lines))
        lines.append("")

    conn.close()
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: Daily health metrics
# ---------------------------------------------------------------------------

@mcp.tool()
def get_health_metrics(days: int = 7) -> str:
    """
    Get daily health metrics from Apple Health or Garmin for the last N days
    (default 7). Includes steps, active calories, sleep, HRV, resting HR.
    """
    start, end = _fmt_date_range(days)
    conn = get_connection()
    rows = conn.execute("""
        SELECT date, steps, active_calories, resting_calories,
               distance_km, resting_heart_rate, hrv, sleep_hours
        FROM daily_health
        WHERE date BETWEEN ? AND ?
        ORDER BY date DESC
    """, (start, end)).fetchall()
    conn.close()

    if not rows:
        return (
            f"No health metrics in the last {days} days. "
            "Import an Apple Health export or Garmin CSV to populate this."
        )

    lines = [f"## Daily Health Metrics — last {days} days\n"]
    for r in rows:
        steps  = f"{r['steps']:,}" if r["steps"] else "–"
        acal   = f"{r['active_calories']:.0f}" if r["active_calories"] else "–"
        sleep  = f"{r['sleep_hours']:.1f}h" if r["sleep_hours"] else "–"
        rhr    = f"{r['resting_heart_rate']}bpm" if r["resting_heart_rate"] else "–"
        hrv    = f"HRV {r['hrv']:.0f}" if r["hrv"] else ""
        lines.append(
            f"**{r['date']}**  Steps:{steps}  Active:{acal}kcal  "
            f"Sleep:{sleep}  RHR:{rhr}  {hrv}"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: TDEE analysis
# ---------------------------------------------------------------------------

@mcp.tool()
def get_tdee_analysis_tool(days: int = 28) -> str:
    """
    Get an adjusted TDEE (Total Daily Energy Expenditure) estimate for the
    last N days (default 28). Accounts for logging gaps and activity variance.
    Returns rest-day and training-day calorie targets.
    """
    result = get_tdee_analysis(days)

    if result.get("adjusted_tdee") is None:
        return result["notes"][0] if result.get("notes") else "Insufficient data."

    lines = [
        f"## TDEE Analysis — last {days} days\n",
        f"**MacroFactor estimate:**  {result['macrofactor_avg'] or '–'} kcal/day",
        f"**Your logging rate:**     {result['logging_rate']:.0%} "
        f"({result['logging_days']}/{result['total_days']} days)",
        f"**Avg calories (logged days):** {result['logged_avg_calories'] or '–'} kcal",
        f"**Weight change:**         {result['weight_change_kg']:+.1f} kg"
        if result["weight_change_kg"] is not None else "**Weight change:** insufficient data",
        f"**Gap correction:**        {result['gap_correction_kcal']:+.0f} kcal/day",
        f"\n**→ Adjusted TDEE:        {result['adjusted_tdee']} kcal/day**",
        f"\n**Rest day target:**       {result['rest_day_tdee']} kcal",
        f"**Training day target:**   {result['active_day_tdee']} kcal",
        f"  (+{result['active_day_addition']} kcal for activity)" if result["active_day_addition"] else "",
    ]

    if result.get("notes"):
        lines.append("\n**Notes:**")
        for n in result["notes"]:
            lines.append(f"  • {n}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: User profile
# ---------------------------------------------------------------------------

@mcp.tool()
def get_user_profile() -> str:
    """
    Get the user's stored profile: fitness background, tendencies, preferences,
    goal history, and any context notes saved from previous sessions.
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT key, value, updated_at FROM user_profile ORDER BY key"
    ).fetchall()
    conn.close()

    if not rows:
        return (
            "No profile saved yet. Use update_user_profile() to store context "
            "such as training background, preferences, and lifestyle notes."
        )

    lines = ["## User Profile\n"]
    for r in rows:
        lines.append(f"**{r['key']}** (updated {r['updated_at'][:10]})\n  {r['value']}\n")

    return "\n".join(lines)


@mcp.tool()
def update_user_profile(key: str, value: str) -> str:
    """
    Store or update a user profile attribute.
    Use this to record training background, lifestyle context, preferences,
    tendencies, injuries, schedule patterns, etc.

    Examples:
      key="training_background", value="5 years of gym training, mostly hypertrophy..."
      key="schedule_pattern", value="Works Mon-Fri office job, trains Tue/Thu/Sat..."
      key="nutrition_tendencies", value="Tends to underlog on weekends and social occasions..."
    """
    conn = get_connection()
    conn.execute("""
        INSERT INTO user_profile (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP
    """, (key, value))
    conn.commit()
    conn.close()
    return f"Profile updated: {key}"


# ---------------------------------------------------------------------------
# Tool: Goals
# ---------------------------------------------------------------------------

@mcp.tool()
def get_current_goal() -> str:
    """
    Get the current active fitness goal, including description and target date.
    Also shows recent achieved goals for context.
    """
    conn = get_connection()
    active = conn.execute(
        "SELECT * FROM goals WHERE status='active' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    achieved = conn.execute(
        "SELECT * FROM goals WHERE status='achieved' ORDER BY completed_at DESC LIMIT 5"
    ).fetchall()
    conn.close()

    lines = []
    if active:
        lines.append(f"## Current Goal\n")
        lines.append(f"**{active['title']}**")
        if active["description"]:
            lines.append(active["description"])
        if active["target_date"]:
            lines.append(f"Target date: {active['target_date']}")
        lines.append(f"Set on: {active['created_at'][:10]}")
    else:
        lines.append("No active goal set. Use set_goal() to define one.")

    if achieved:
        lines.append("\n## Previously Achieved Goals")
        for g in achieved:
            lines.append(
                f"✓ **{g['title']}** — achieved {(g['completed_at'] or '')[:10]}"
            )

    return "\n".join(lines)


@mcp.tool()
def set_goal(title: str, description: str, target_date: str = "") -> str:
    """
    Set a new active fitness goal. The previous active goal will be marked
    as 'achieved' if it was completed, or left as-is if being replaced.

    Args:
      title:       Short goal title, e.g. "Lose 5kg by summer"
      description: Detailed description of the goal and approach
      target_date: Optional ISO date string, e.g. "2025-06-01"
    """
    conn = get_connection()
    conn.execute("""
        INSERT INTO goals (title, description, target_date, status)
        VALUES (?, ?, ?, 'active')
    """, (title, description, target_date or None))
    conn.commit()
    conn.close()
    return f"Goal set: {title}"


@mcp.tool()
def complete_goal(goal_title: str, notes: str = "") -> str:
    """
    Mark the current active goal as achieved.

    Args:
      goal_title: Title of the goal to mark complete (must match exactly)
      notes:      Optional notes on how it was achieved
    """
    conn = get_connection()
    conn.execute("""
        UPDATE goals
        SET status='achieved', completed_at=CURRENT_TIMESTAMP, notes=?
        WHERE title=? AND status='active'
    """, (notes or None, goal_title))
    conn.commit()
    conn.close()
    return f"Goal marked as achieved: {goal_title}"


# ---------------------------------------------------------------------------
# Tool: Check-in history
# ---------------------------------------------------------------------------

@mcp.tool()
def save_checkin_summary(summary: str, plan_for_week: str, tdee_used: float = 0.0) -> str:
    """
    Save a summary of this check-in session to the database.
    Call this at the END of each coaching session.

    Args:
      summary:       A 2-4 sentence summary of what was discussed and key observations.
      plan_for_week: The plan agreed for the coming week (training, nutrition targets).
      tdee_used:     The TDEE figure used for this week's plan (0 if not applicable).
    """
    conn = get_connection()
    conn.execute("""
        INSERT INTO checkins (date, summary, plan_for_week, tdee_used)
        VALUES (date('now'), ?, ?, ?)
    """, (summary, plan_for_week, tdee_used or None))
    conn.commit()
    conn.close()
    return "Check-in saved."


@mcp.tool()
def get_checkin_history(limit: int = 5) -> str:
    """
    Retrieve the last N check-in session summaries (default 5).
    Useful context at the start of a new session.
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT date, summary, plan_for_week, tdee_used FROM checkins ORDER BY date DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()

    if not rows:
        return "No previous check-ins recorded yet."

    lines = [f"## Last {limit} Check-ins\n"]
    for r in rows:
        tdee_str = f"  *(TDEE used: {r['tdee_used']:.0f} kcal)*" if r["tdee_used"] else ""
        lines.append(f"### {r['date']}{tdee_str}")
        lines.append(f"**Summary:** {r['summary']}")
        lines.append(f"**Plan:** {r['plan_for_week']}\n")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: Strava sync
# ---------------------------------------------------------------------------

@mcp.tool()
def sync_strava(days_back: int = 30) -> str:
    """
    Trigger a Strava sync to fetch the latest activities.
    Requires Strava to have been authorised first (run strava_auth.py once).

    Args:
      days_back: How many days of history to fetch (default 30).
    """
    try:
        from src.ingestion.strava import sync_activities
        inserted = sync_activities(days_back=days_back)
        return f"Strava sync complete. {inserted} new activity(s) added."
    except RuntimeError as e:
        return f"Strava sync failed: {e}"
    except Exception as e:
        return f"Strava sync error: {e}"


# ---------------------------------------------------------------------------
# Tool: fitness background (long-form narrative history)
# ---------------------------------------------------------------------------

BACKGROUND_FILE = Path(__file__).parent.parent.parent / "config" / "fitness_background.md"

@mcp.tool()
def get_fitness_background() -> str:
    """
    Load the user's written fitness background — training history, lifestyle,
    injuries, what has and hasn't worked, and long-term context.
    Read this at the start of the first session with a new user, or whenever
    deep background context is needed.
    """
    if not BACKGROUND_FILE.exists():
        return "No fitness background file found."
    content = BACKGROUND_FILE.read_text(encoding="utf-8").strip()
    if not content or all(line.startswith("#") or not line.strip() for line in content.splitlines()):
        return (
            "Fitness background file exists but hasn't been filled in yet. "
            "Ask the user about their training history to build this context."
        )
    return content


# ---------------------------------------------------------------------------
# Prompt: system context for the fitness trainer persona
# ---------------------------------------------------------------------------

@mcp.prompt()
def fitness_trainer() -> str:
    """Load the fitness trainer system prompt."""
    prompt_path = Path(__file__).parent.parent.parent / "config" / "system_prompt.md"
    if prompt_path.exists():
        return prompt_path.read_text()
    return "You are a knowledgeable, encouraging personal fitness trainer and nutritionist."


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run():
    initialize()  # ensure DB exists
    mcp.run()


if __name__ == "__main__":
    run()
