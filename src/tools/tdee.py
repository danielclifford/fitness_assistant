"""
TDEE (Total Daily Energy Expenditure) estimation.

MacroFactor's TDEE estimate is based on the food you log and your weight trend.
The problem:
  1. On unlogged days you typically eat MORE than on logged days
  2. MacroFactor sees a calorie surplus (or smaller deficit) relative to
     its incomplete intake estimate, so it lowers its TDEE estimate.
  3. Activity varies a lot day to day, meaning a flat weekly TDEE obscures
     the difference between a heavy training day and a rest day.

Our approach:
  a) Detect logging gaps (days where calories logged < threshold or not logged at all).
  b) Estimate true intake on those days by looking at the gap between MacroFactor's
     TDEE and the actual weight change for that period — the delta tells us how much
     energy was "missing" from the logs.
  c) Cross-reference Strava / Apple Health active calories for activity adjustment.
  d) Produce a corrected weekly TDEE and a per-day-type estimate.
"""

from datetime import date, timedelta
from src.database import get_connection


def _date_range(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def get_tdee_analysis(days: int = 28) -> dict:
    """
    Analyse the last `days` days and return an adjusted TDEE estimate.

    Returns a dict with:
      macrofactor_avg     — raw average from MacroFactor
      logged_avg_calories — average calories on logged days only
      logging_rate        — fraction of days with complete logs
      weight_change_kg    — actual weight change over the period
      implied_surplus     — kcal/day implied by weight change
      adjusted_tdee       — our best estimate of true TDEE
      active_day_addition — extra kcal to eat on training days
      rest_day_tdee       — recommended target on rest days
      active_day_tdee     — recommended target on training days
      notes               — list of explanation strings
    """
    conn = get_connection()
    end_date = date.today()
    start_date = end_date - timedelta(days=days)

    # --- Nutrition logs ---
    rows = conn.execute("""
        SELECT date, calories, macrofactor_tdee, is_complete_log
        FROM nutrition_logs
        WHERE date BETWEEN ? AND ?
        ORDER BY date
    """, (start_date.isoformat(), end_date.isoformat())).fetchall()

    logged_dates = {r["date"] for r in rows}
    total_days = (end_date - start_date).days + 1
    complete_rows = [r for r in rows if r["is_complete_log"] == 1 and r["calories"]]
    logging_days = len(complete_rows)
    logging_rate = logging_days / total_days if total_days > 0 else 0

    logged_avg_cal = (
        sum(r["calories"] for r in complete_rows) / logging_days
        if logging_days > 0 else None
    )

    mf_tdee_rows = [r["macrofactor_tdee"] for r in rows if r["macrofactor_tdee"]]
    macrofactor_avg = sum(mf_tdee_rows) / len(mf_tdee_rows) if mf_tdee_rows else None

    # --- Weight trend ---
    weight_rows = conn.execute("""
        SELECT date, weight_kg FROM weight_logs
        WHERE date BETWEEN ? AND ?
        ORDER BY date
    """, (start_date.isoformat(), end_date.isoformat())).fetchall()

    weight_change_kg = None
    if len(weight_rows) >= 2:
        weight_change_kg = round(
            weight_rows[-1]["weight_kg"] - weight_rows[0]["weight_kg"], 2
        )

    # --- Activity data ---
    activity_rows = conn.execute("""
        SELECT start_date, calories, kilojoules, sport_type, moving_time_seconds
        FROM activities
        WHERE date(start_date) BETWEEN ? AND ?
    """, (start_date.isoformat(), end_date.isoformat())).fetchall()

    # Strava calories (prefer calories field; fallback to kilojoules * 0.239)
    active_dates = set()
    strava_cal_by_date = {}
    for a in activity_rows:
        d = a["start_date"][:10]
        active_dates.add(d)
        cal = a["calories"] or (a["kilojoules"] * 0.239 if a["kilojoules"] else 0)
        strava_cal_by_date[d] = strava_cal_by_date.get(d, 0) + cal

    avg_active_cal = (
        sum(strava_cal_by_date.values()) / len(strava_cal_by_date)
        if strava_cal_by_date else 0
    )

    # --- Apple Health active calories (supplement / override Strava) ---
    health_rows = conn.execute("""
        SELECT date, active_calories FROM daily_health
        WHERE date BETWEEN ? AND ?
    """, (start_date.isoformat(), end_date.isoformat())).fetchall()
    health_cal_by_date = {r["date"]: r["active_calories"] for r in health_rows if r["active_calories"]}

    # --- Core TDEE adjustment ---
    notes = []

    if macrofactor_avg is None and logged_avg_cal is None:
        return {
            "adjusted_tdee": None,
            "notes": ["Not enough data yet — import your MacroFactor CSV first."],
        }

    base_tdee = macrofactor_avg or logged_avg_cal

    # Step 1: Logging gap correction
    # If logging rate is low, MacroFactor underestimates intake.
    # We use weight trend to infer "missing" calories.
    gap_correction = 0.0
    if weight_change_kg is not None and logging_rate < 0.85:
        # Expected weight change at MacroFactor's TDEE estimate
        # (assumes logged_avg_cal ≈ intake on logged days)
        if logged_avg_cal and base_tdee:
            logged_surplus_per_day = logged_avg_cal - base_tdee
            expected_weight_change = (logged_surplus_per_day * total_days) / 7700
            actual_vs_expected = weight_change_kg - expected_weight_change
            # Each 1 kg unexplained weight gain ≈ 7700 kcal over the period
            unexplained_kcal = actual_vs_expected * 7700
            gap_correction = unexplained_kcal / total_days
            if abs(gap_correction) > 50:
                notes.append(
                    f"Weight trend implies {abs(gap_correction):.0f} kcal/day "
                    f"{'more' if gap_correction > 0 else 'less'} eaten than logged "
                    f"(likely on the {total_days - logging_days} unlogged days)."
                )

    adjusted_tdee = base_tdee + gap_correction

    # Step 2: Activity variance
    # Compute average extra burn on workout days vs rest days
    activity_addition = 0
    if strava_cal_by_date:
        activity_addition = round(avg_active_cal * 0.7, 0)  # ~70% net after METs
        notes.append(
            f"On training days you burn ~{activity_addition:.0f} extra kcal (from Strava)."
        )

    if logging_rate < 0.6:
        notes.append(
            f"⚠️  Only {logging_rate:.0%} of days are fully logged. "
            "The adjustment is an estimate — the more consistently you log, the more accurate this gets."
        )

    rest_day_tdee = round(adjusted_tdee)
    active_day_tdee = round(adjusted_tdee + activity_addition)

    conn.close()

    return {
        "macrofactor_avg":      round(macrofactor_avg, 0) if macrofactor_avg else None,
        "logged_avg_calories":  round(logged_avg_cal, 0) if logged_avg_cal else None,
        "logging_rate":         round(logging_rate, 2),
        "logging_days":         logging_days,
        "total_days":           total_days,
        "weight_change_kg":     weight_change_kg,
        "gap_correction_kcal":  round(gap_correction, 0),
        "adjusted_tdee":        round(adjusted_tdee, 0),
        "active_day_addition":  round(activity_addition, 0),
        "rest_day_tdee":        rest_day_tdee,
        "active_day_tdee":      active_day_tdee,
        "notes":                notes,
    }
