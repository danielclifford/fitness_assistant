"""
Strong app CSV importer.

How to export:
  Strong → Settings → Export Data → Export Workout Data (CSV)

Drop the CSV into data/imports/ and run import_data.py.

Strong CSV format (semicolon-delimited):
  Date;Workout Name;Duration;Exercise Name;Set Order;Weight;Reps;
  Distance;Seconds;Notes;Workout Notes;RPE
"""

import csv
import re
from datetime import date, datetime
from pathlib import Path
from src.database import get_connection


def _norm(s: str) -> str:
    return re.sub(r"[\s_]+", " ", s.strip().lower())


def _parse_float(val: str) -> float | None:
    val = (val or "").strip().replace(",", "")
    try:
        return float(val) if val else None
    except ValueError:
        return None


def _parse_int(val: str) -> int | None:
    f = _parse_float(val)
    return int(f) if f is not None else None


def _parse_duration(val: str) -> int | None:
    """Convert Strong duration string (e.g. '1h 5m' or '45m') to minutes."""
    if not val:
        return None
    val = val.strip()
    hours = re.search(r"(\d+)\s*h", val)
    mins = re.search(r"(\d+)\s*m", val)
    total = 0
    if hours:
        total += int(hours.group(1)) * 60
    if mins:
        total += int(mins.group(1))
    return total if total > 0 else None


def import_csv(filepath: str | Path) -> dict:
    """
    Parse a Strong CSV and upsert into workouts and workout_sets.
    Returns {"workouts": N, "sets": N}.
    """
    filepath = Path(filepath)

    # Strong uses semicolons; detect delimiter from first line
    with open(filepath, encoding="utf-8-sig") as f:
        sample = f.readline()
    delimiter = ";" if sample.count(";") > sample.count(",") else ","

    workout_count = 0
    set_count = 0
    conn = get_connection()

    with open(filepath, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        # Normalise header names
        raw_headers = reader.fieldnames or []
        norm_headers = {_norm(h): h for h in raw_headers}

        def col(canonical: str):
            """Return the actual header name for a normalised key."""
            for variant in [canonical, canonical.replace(" ", "_")]:
                if variant in norm_headers:
                    return norm_headers[variant]
            return None

        date_col      = col("date")
        workout_col   = col("workout name")
        duration_col  = col("duration")
        exercise_col  = col("exercise name")
        set_order_col = col("set order")
        weight_col    = col("weight")
        reps_col      = col("reps")
        notes_col     = col("notes")
        w_notes_col   = col("workout notes")
        rpe_col       = col("rpe")

        # Group rows by (date, workout_name)
        workouts: dict[tuple, dict] = {}

        for row in reader:
            raw_date = (row.get(date_col) or "").strip()
            if not raw_date:
                continue

            try:
                # Strong format: "2024-01-15 08:30:00"
                if " " in raw_date:
                    row_date = datetime.strptime(raw_date, "%Y-%m-%d %H:%M:%S").date()
                elif "T" in raw_date:
                    row_date = datetime.fromisoformat(raw_date).date()
                else:
                    row_date = date.fromisoformat(raw_date)
            except ValueError:
                continue

            workout_name = (row.get(workout_col) or "Workout").strip()
            key = (row_date.isoformat(), workout_name)

            if key not in workouts:
                workouts[key] = {
                    "date": row_date.isoformat(),
                    "name": workout_name,
                    "duration": _parse_duration(row.get(duration_col) or ""),
                    "notes": (row.get(w_notes_col) or "").strip() or None,
                    "sets": [],
                }

            workouts[key]["sets"].append({
                "exercise": (row.get(exercise_col) or "").strip(),
                "set_num":  _parse_int(row.get(set_order_col) or ""),
                "weight_kg": _parse_float(row.get(weight_col) or ""),
                "reps":     _parse_int(row.get(reps_col) or ""),
                "rpe":      _parse_float(row.get(rpe_col) or ""),
                "notes":    (row.get(notes_col) or "").strip() or None,
            })

    # Insert workouts and sets
    for key, w in workouts.items():
        # Check if workout already exists
        existing = conn.execute(
            "SELECT id FROM workouts WHERE date=? AND name=? AND source='strong'",
            (w["date"], w["name"])
        ).fetchone()

        if existing:
            workout_id = existing["id"]
            # Remove existing sets to replace with fresh import
            conn.execute("DELETE FROM workout_sets WHERE workout_id=?", (workout_id,))
        else:
            cur = conn.execute("""
                INSERT INTO workouts (date, name, duration_minutes, notes, source)
                VALUES (?, ?, ?, ?, 'strong')
            """, (w["date"], w["name"], w["duration"], w["notes"]))
            workout_id = cur.lastrowid
            workout_count += 1

        for s in w["sets"]:
            if not s["exercise"]:
                continue
            conn.execute("""
                INSERT INTO workout_sets
                    (workout_id, exercise_name, set_number, weight_kg, reps, rpe, notes)
                VALUES (?,?,?,?,?,?,?)
            """, (workout_id, s["exercise"], s["set_num"],
                  s["weight_kg"], s["reps"], s["rpe"], s["notes"]))
            set_count += 1

    conn.commit()
    conn.close()
    return {"workouts": workout_count, "sets": set_count}
