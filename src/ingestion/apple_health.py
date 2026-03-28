"""
Apple Health XML importer.

How to export:
  iPhone → Health app → your profile picture (top-right) → Export All Health Data
  This creates export.zip. Unzip it; you want the file export.xml inside.

Drop export.xml into data/imports/ and run import_data.py.

We extract:
  - Step count
  - Active energy burned
  - Resting energy burned
  - Walking + running distance
  - Resting heart rate
  - HRV (SDNN)
  - Flights climbed
  - Sleep analysis (total hours asleep)
  - Body mass (weight)
"""

import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from src.database import get_connection

# Apple Health quantity type identifiers we care about
_QUANTITY_TYPES = {
    "HKQuantityTypeIdentifierStepCount":              "steps",
    "HKQuantityTypeIdentifierActiveEnergyBurned":     "active_calories",
    "HKQuantityTypeIdentifierBasalEnergyBurned":      "resting_calories",
    "HKQuantityTypeIdentifierDistanceWalkingRunning": "distance_km",
    "HKQuantityTypeIdentifierFlightsClimbed":         "floors_climbed",
    "HKQuantityTypeIdentifierRestingHeartRate":       "resting_heart_rate",
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": "hrv",
    "HKQuantityTypeIdentifierBodyMass":               "weight_kg",
}

_SLEEP_TYPE = "HKCategoryTypeIdentifierSleepAnalysis"
_SLEEP_ASLEEP_VALUES = {
    "HKCategoryValueSleepAnalysisAsleepCore",
    "HKCategoryValueSleepAnalysisAsleepDeep",
    "HKCategoryValueSleepAnalysisAsleepREM",
    "HKCategoryValueSleepAnalysisAsleep",  # older iOS
}


def _parse_date(s: str) -> date | None:
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _parse_datetime(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s.replace(" +0000", "+00:00"))
    except (ValueError, TypeError):
        try:
            return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None


def import_xml(filepath: str | Path) -> dict:
    """
    Parse Apple Health export.xml and upsert into daily_health and weight_logs.
    Returns {"health_rows": N, "weight_rows": N}.
    """
    filepath = Path(filepath)
    print(f"Parsing {filepath.name} — this may take a minute for large exports...")

    # Daily accumulators
    # Each key is a date string; value is dict of metric totals
    daily: dict[str, dict] = defaultdict(lambda: {
        "steps": 0.0,
        "active_calories": 0.0,
        "resting_calories": 0.0,
        "distance_km": 0.0,
        "floors_climbed": 0.0,
        "resting_heart_rate": [],   # list of readings to average
        "hrv": [],
        "sleep_seconds": 0.0,
        "weight_kg": [],
    })

    context = ET.iterparse(filepath, events=("end",))

    for event, elem in context:
        if elem.tag == "Record":
            rtype = elem.get("type", "")
            metric = _QUANTITY_TYPES.get(rtype)

            if metric:
                start_str = elem.get("startDate") or elem.get("creationDate", "")
                row_date = _parse_date(start_str)
                if row_date is None:
                    elem.clear()
                    continue

                try:
                    value = float(elem.get("value", "0") or "0")
                except ValueError:
                    elem.clear()
                    continue

                d = row_date.isoformat()

                if metric == "distance_km":
                    # Apple stores in metres for some sources, km for others
                    unit = elem.get("unit", "km")
                    if unit in ("m", "mi"):
                        value = value / 1000 if unit == "m" else value * 1.60934
                    daily[d]["distance_km"] += value

                elif metric in ("steps", "active_calories", "resting_calories", "floors_climbed"):
                    daily[d][metric] += value

                elif metric in ("resting_heart_rate", "hrv"):
                    daily[d][metric].append(value)

                elif metric == "weight_kg":
                    unit = elem.get("unit", "kg")
                    if unit == "lb":
                        value = round(value * 0.453592, 2)
                    daily[d]["weight_kg"].append(value)

            elif rtype == _SLEEP_TYPE:
                val = elem.get("value", "")
                if val in _SLEEP_ASLEEP_VALUES:
                    start_dt = _parse_datetime(elem.get("startDate", ""))
                    end_dt = _parse_datetime(elem.get("endDate", ""))
                    if start_dt and end_dt:
                        duration_s = (end_dt - start_dt).total_seconds()
                        if 0 < duration_s < 86400:
                            d = start_dt.date().isoformat()
                            daily[d]["sleep_seconds"] += duration_s

            elem.clear()

    # Write to database
    conn = get_connection()
    health_count = 0
    weight_count = 0

    for d, vals in sorted(daily.items()):
        rhr = None
        if vals["resting_heart_rate"]:
            rhr = round(sum(vals["resting_heart_rate"]) / len(vals["resting_heart_rate"]))

        hrv = None
        if vals["hrv"]:
            hrv = round(sum(vals["hrv"]) / len(vals["hrv"]), 1)

        sleep_h = round(vals["sleep_seconds"] / 3600, 2) if vals["sleep_seconds"] else None

        conn.execute("""
            INSERT INTO daily_health
                (date, steps, active_calories, resting_calories, distance_km,
                 floors_climbed, resting_heart_rate, hrv, sleep_hours, source)
            VALUES (?,?,?,?,?,?,?,?,?,'apple_health')
            ON CONFLICT(date) DO UPDATE SET
                steps=excluded.steps,
                active_calories=excluded.active_calories,
                resting_calories=excluded.resting_calories,
                distance_km=excluded.distance_km,
                floors_climbed=excluded.floors_climbed,
                resting_heart_rate=excluded.resting_heart_rate,
                hrv=excluded.hrv,
                sleep_hours=excluded.sleep_hours,
                source='apple_health'
        """, (
            d,
            int(vals["steps"]) or None,
            round(vals["active_calories"], 1) or None,
            round(vals["resting_calories"], 1) or None,
            round(vals["distance_km"], 2) or None,
            int(vals["floors_climbed"]) or None,
            rhr,
            hrv,
            sleep_h,
        ))
        health_count += 1

        for w in vals["weight_kg"]:
            conn.execute("""
                INSERT INTO weight_logs (date, weight_kg, source)
                VALUES (?, ?, 'apple_health')
                ON CONFLICT(date, source) DO UPDATE SET weight_kg=excluded.weight_kg
            """, (d, w))
            weight_count += 1

    conn.commit()
    conn.close()
    return {"health_rows": health_count, "weight_rows": weight_count}
