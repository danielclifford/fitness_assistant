"""
TDEE (Total Daily Energy Expenditure) estimation.

Why MacroFactor's estimate is biased downward:
  MacroFactor uses CICO: TDEE = intake - weight_change_calories.
  On unlogged days it has no intake data, so it treats those days as if intake
  matched its running estimate. In reality unlogged days (social meals, weekends)
  tend to be significantly higher calorie. This makes MacroFactor see an
  unexplained weight trajectory and lower its TDEE estimate.

Why we can't simply use Apple Health resting+active calories as TDEE:
  Apple Health aggregates from multiple sources (Garmin AND iPhone/Apple Watch).
  Both devices write to the same HKQuantityTypeIdentifierBasalEnergyBurned field,
  so resting_calories ends up roughly 2× a realistic BMR (~3500 vs ~1750 expected).
  We flag this but do not use resting_calories in the calculation.

Our synthesis approach:
  1. WEIGHT TREND (ground truth)
     Weight change in kg × 7700 kcal = net energy balance over the period.
     This is the most reliable signal we have.

  2. LOGGED CALORIE ANCHOR
     On days with complete MacroFactor logs we know actual intake with reasonable
     accuracy. This is the CICO anchor.

  3. UNLOGGED DAY ESTIMATE (explicit assumption)
     Unlogged days are known to be higher-calorie (social meals, weekends). We
     apply a premium multiplier (default 35%) over logged-day average. This is
     a stated assumption, not a derived figure. Claude should ask the user to
     calibrate this over time.

  4. STRAVA CALORIES (training day premium)
     Strava calories are a single reliable source per workout. We use them to
     compute the extra burn on training days vs rest days.

  5. APPLE HEALTH ACTIVE CALORIES (cross-check only)
     Used to sanity-check training day activity levels. Not used in the primary
     TDEE estimate due to potential double-counting.

Formula:
  estimated_daily_intake = logged_avg × (logging_rate + (1 - logging_rate) × unlogged_premium)
  adjusted_tdee = estimated_daily_intake - (net_balance_kcal / total_days)
  rest_day_tdee = adjusted_tdee
  active_day_tdee = adjusted_tdee + strava_avg_training_cal
"""

from datetime import date, timedelta
from src.database import get_connection

# Default premium for unlogged days over logged-day average.
# 1.35 = assume unlogged days are ~35% higher calorie than logged days.
# Based on user context: unlogged days are typically social/weekend occasions.
# This should be reviewed and calibrated each session.
UNLOGGED_DAY_PREMIUM = 1.35


def get_tdee_analysis(days: int = 28) -> dict:
    """
    Analyse the last `days` days and return an adjusted TDEE estimate.

    Returns a dict with:
      macrofactor_avg           — raw MacroFactor TDEE (biased down, shown for reference)
      logged_avg_calories       — avg kcal on fully logged days
      estimated_unlogged_cal    — estimated avg kcal on unlogged days (logged_avg × premium)
      unlogged_premium_pct      — the premium multiplier used (as a percentage over logged avg)
      logging_rate              — fraction of days with complete logs
      weight_change_kg          — actual weight change over the period
      net_balance_kcal_per_day  — net energy balance implied by weight trend (+ = surplus)
      adjusted_tdee             — our best estimate of true TDEE
      rest_day_tdee             — adjusted_tdee (baseline, no training)
      active_day_tdee           — adjusted_tdee + average Strava training session burn
      strava_avg_training_cal   — average Strava calories on training days
      apple_active_avg          — Apple Health active_calories average (cross-check only)
      apple_active_training     — Apple Health active_calories avg on training days
      apple_active_rest         — Apple Health active_calories avg on rest days
      notes                     — list of explanation strings
    """
    conn = get_connection()
    end_date = date.today()
    start_date = end_date - timedelta(days=days)
    notes = []

    # ------------------------------------------------------------------ #
    # 1. Nutrition logs                                                    #
    # ------------------------------------------------------------------ #
    rows = conn.execute("""
        SELECT date, calories, macrofactor_tdee, is_complete_log
        FROM nutrition_logs
        WHERE date BETWEEN ? AND ?
        ORDER BY date
    """, (start_date.isoformat(), end_date.isoformat())).fetchall()

    total_days = (end_date - start_date).days + 1
    complete_rows = [r for r in rows if r["is_complete_log"] == 1 and r["calories"]]
    logging_days = len(complete_rows)
    unlogged_days = total_days - logging_days
    logging_rate = logging_days / total_days if total_days > 0 else 0

    logged_avg_cal = (
        sum(r["calories"] for r in complete_rows) / logging_days
        if logging_days > 0 else None
    )

    mf_tdee_rows = [r["macrofactor_tdee"] for r in rows if r["macrofactor_tdee"]]
    macrofactor_avg = sum(mf_tdee_rows) / len(mf_tdee_rows) if mf_tdee_rows else None

    # ------------------------------------------------------------------ #
    # 2. Weight trend (prefer MacroFactor trend weight — smoother)        #
    # ------------------------------------------------------------------ #
    weight_rows = conn.execute("""
        SELECT date, weight_kg FROM weight_logs
        WHERE date BETWEEN ? AND ?
          AND source = 'macrofactor_trend'
        ORDER BY date
    """, (start_date.isoformat(), end_date.isoformat())).fetchall()

    if len(weight_rows) < 2:
        weight_rows = conn.execute("""
            SELECT date, weight_kg FROM weight_logs
            WHERE date BETWEEN ? AND ?
            ORDER BY date
        """, (start_date.isoformat(), end_date.isoformat())).fetchall()

    weight_change_kg = None
    net_balance_per_day = None
    if len(weight_rows) >= 2:
        weight_change_kg = round(
            weight_rows[-1]["weight_kg"] - weight_rows[0]["weight_kg"], 2
        )
        net_balance_per_day = round((weight_change_kg * 7700) / total_days, 1)

    # ------------------------------------------------------------------ #
    # 3. Strava training days and calories                                #
    # Walks excluded — they are daily movement, not training sessions.   #
    # ------------------------------------------------------------------ #
    _WALK_TYPES = {"Walk", "Hike"}
    activity_rows = conn.execute("""
        SELECT date(start_date) AS day, sport_type, calories, kilojoules
        FROM activities
        WHERE date(start_date) BETWEEN ? AND ?
    """, (start_date.isoformat(), end_date.isoformat())).fetchall()

    strava_cal_by_date = {}
    training_dates = set()
    for a in activity_rows:
        if a["sport_type"] in _WALK_TYPES:
            continue
        d = a["day"]
        training_dates.add(d)
        cal = a["calories"] or (a["kilojoules"] * 0.239 if a["kilojoules"] else 0)
        if cal:
            strava_cal_by_date[d] = strava_cal_by_date.get(d, 0) + cal

    strava_avg_training_cal = (
        round(sum(strava_cal_by_date.values()) / len(strava_cal_by_date))
        if strava_cal_by_date else None
    )

    # ------------------------------------------------------------------ #
    # 4. Apple Health active_calories (cross-check only)                  #
    # NOTE: resting_calories is NOT used — it is ~2× expected BMR,        #
    # indicating double-counting from multiple sources (Garmin + iPhone). #
    # ------------------------------------------------------------------ #
    health_rows = conn.execute("""
        SELECT date, active_calories FROM daily_health
        WHERE date BETWEEN ? AND ?
          AND active_calories IS NOT NULL
    """, (start_date.isoformat(), end_date.isoformat())).fetchall()

    apple_active_by_date = {r["date"]: r["active_calories"] for r in health_rows}
    apple_active_training = [v for d, v in apple_active_by_date.items() if d in training_dates]
    apple_active_rest = [v for d, v in apple_active_by_date.items() if d not in training_dates]

    apple_active_avg = (
        round(sum(apple_active_by_date.values()) / len(apple_active_by_date))
        if apple_active_by_date else None
    )
    apple_active_training_avg = (
        round(sum(apple_active_training) / len(apple_active_training))
        if apple_active_training else None
    )
    apple_active_rest_avg = (
        round(sum(apple_active_rest) / len(apple_active_rest))
        if apple_active_rest else None
    )

    # ------------------------------------------------------------------ #
    # 5. Adjusted TDEE                                                    #
    #                                                                     #
    # Core formula:                                                       #
    #   est_avg_intake = logged_avg × (logging_rate                       #
    #                   + (1 - logging_rate) × UNLOGGED_DAY_PREMIUM)      #
    #   adjusted_tdee  = est_avg_intake - net_balance_per_day             #
    # ------------------------------------------------------------------ #
    adjusted_tdee = None
    estimated_unlogged_cal = None

    if logged_avg_cal is not None:
        estimated_unlogged_cal = round(logged_avg_cal * UNLOGGED_DAY_PREMIUM)
        est_avg_intake = logged_avg_cal * (
            logging_rate + (1 - logging_rate) * UNLOGGED_DAY_PREMIUM
        )
        if net_balance_per_day is not None:
            adjusted_tdee = round(est_avg_intake - net_balance_per_day)
        else:
            adjusted_tdee = round(est_avg_intake)

    rest_day_tdee = adjusted_tdee

    # For the training day premium, prefer Strava calories (single reliable source).
    # Strava often lacks calorie data when Garmin handles HR — fall back to the
    # difference between Apple Health active_calories on training vs rest days.
    training_premium = None
    training_premium_source = None
    if strava_avg_training_cal:
        training_premium = strava_avg_training_cal
        training_premium_source = "Strava"
    elif apple_active_training_avg and apple_active_rest_avg:
        training_premium = round(apple_active_training_avg - apple_active_rest_avg)
        training_premium_source = "Apple Health delta"

    active_day_tdee = (
        round(adjusted_tdee + training_premium)
        if adjusted_tdee and training_premium else adjusted_tdee
    )

    # ------------------------------------------------------------------ #
    # 6. Notes                                                            #
    # ------------------------------------------------------------------ #
    if macrofactor_avg and adjusted_tdee:
        diff = adjusted_tdee - round(macrofactor_avg)
        notes.append(
            f"MacroFactor estimates {macrofactor_avg:.0f} kcal/day TDEE. "
            f"Our adjusted estimate is {adjusted_tdee} kcal/day "
            f"(+{diff} kcal), accounting for higher intake on the "
            f"{unlogged_days} unlogged days ({100 - logging_rate*100:.0f}% of the period)."
        )

    if estimated_unlogged_cal and logged_avg_cal:
        notes.append(
            f"Unlogged days assumed at {estimated_unlogged_cal} kcal "
            f"({(UNLOGGED_DAY_PREMIUM - 1)*100:.0f}% above logged average of {logged_avg_cal:.0f} kcal). "
            "This is a calibratable assumption — ask the user to estimate how much more "
            "they typically eat on social/unlogged days to refine this."
        )

    if net_balance_per_day is not None:
        direction = "surplus" if net_balance_per_day > 0 else "deficit"
        notes.append(
            f"Weight trend implies {abs(net_balance_per_day):.0f} kcal/day net {direction} "
            f"over the period (weight change: {weight_change_kg:+.2f} kg)."
        )

    if training_premium and training_premium_source:
        notes.append(
            f"Training day premium: +{training_premium} kcal (source: {training_premium_source}). "
            f"Active day TDEE = {active_day_tdee} kcal."
        )
    elif training_dates and not strava_avg_training_cal:
        notes.append(
            "⚠️  Strava activities have no calorie data (common when Garmin handles HR). "
            "Training day premium cannot be computed from Strava. "
            "If Apple Health active_calories also lacks a rest/training split, "
            "estimate training day premium manually (~400–600 kcal for a typical gym session, "
            "~500–800 kcal for a run)."
        )

    if apple_active_avg:
        notes.append(
            f"Apple Health active calories (cross-check): avg {apple_active_avg} kcal/day overall, "
            f"{apple_active_training_avg or 'n/a'} on training days, "
            f"{apple_active_rest_avg or 'n/a'} on rest days. "
            "Note: resting_calories data excluded due to suspected double-counting "
            "(Garmin + iPhone both writing to same Apple Health field)."
        )

    if logging_rate < 0.6:
        notes.append(
            f"⚠️  Only {logging_rate:.0%} of days are fully logged. "
            "The unlogged day premium assumption has more impact when logging rate is low — "
            "press the user on what a typical unlogged day looks like."
        )

    conn.close()

    return {
        "macrofactor_avg":          round(macrofactor_avg, 0) if macrofactor_avg else None,
        "logged_avg_calories":      round(logged_avg_cal, 0) if logged_avg_cal else None,
        "estimated_unlogged_cal":   estimated_unlogged_cal,
        "unlogged_premium_pct":     round((UNLOGGED_DAY_PREMIUM - 1) * 100),
        "logging_rate":             round(logging_rate, 2),
        "logging_days":             logging_days,
        "unlogged_days":            unlogged_days,
        "total_days":               total_days,
        "weight_change_kg":         weight_change_kg,
        "net_balance_kcal_per_day": net_balance_per_day,
        "adjusted_tdee":            adjusted_tdee,
        "rest_day_tdee":            rest_day_tdee,
        "active_day_tdee":          active_day_tdee,
        "strava_avg_training_cal":  strava_avg_training_cal,
        "training_premium":         training_premium,
        "training_premium_source":  training_premium_source,
        "apple_active_avg":         apple_active_avg,
        "apple_active_training":    apple_active_training_avg,
        "apple_active_rest":        apple_active_rest_avg,
        "notes":                    notes,
    }
