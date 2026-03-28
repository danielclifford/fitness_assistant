"""
Central database module — SQLite connection and schema initialisation.
All other modules import get_connection() from here.
"""

import sqlite3
import os
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "db" / "fitness.db"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def initialize():
    """Create all tables if they don't exist. Safe to run multiple times."""
    conn = get_connection()
    cur = conn.cursor()

    cur.executescript("""
    -- ----------------------------------------------------------------
    -- User profile: flexible key-value store for preferences/context
    -- ----------------------------------------------------------------
    CREATE TABLE IF NOT EXISTS user_profile (
        key   TEXT PRIMARY KEY,
        value TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- ----------------------------------------------------------------
    -- Goals
    -- ----------------------------------------------------------------
    CREATE TABLE IF NOT EXISTS goals (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        title        TEXT NOT NULL,
        description  TEXT,
        target_date  DATE,
        status       TEXT DEFAULT 'active',   -- active / achieved / abandoned
        created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        completed_at TIMESTAMP,
        notes        TEXT
    );

    -- ----------------------------------------------------------------
    -- Check-in sessions (saved at the end of each Claude conversation)
    -- ----------------------------------------------------------------
    CREATE TABLE IF NOT EXISTS checkins (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        date         DATE NOT NULL,
        summary      TEXT,
        plan_for_week TEXT,
        tdee_used    REAL,
        created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- ----------------------------------------------------------------
    -- Strava activities
    -- ----------------------------------------------------------------
    CREATE TABLE IF NOT EXISTS activities (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        strava_id            INTEGER UNIQUE,
        name                 TEXT,
        sport_type           TEXT,
        start_date           TIMESTAMP,
        distance_meters      REAL,
        moving_time_seconds  INTEGER,
        elapsed_time_seconds INTEGER,
        elevation_gain       REAL,
        average_heartrate    REAL,
        max_heartrate        REAL,
        average_watts        REAL,
        kilojoules           REAL,
        calories             REAL,
        suffer_score         INTEGER,
        description          TEXT,
        raw_json             TEXT
    );

    -- ----------------------------------------------------------------
    -- Nutrition logs (from MacroFactor CSV)
    -- ----------------------------------------------------------------
    CREATE TABLE IF NOT EXISTS nutrition_logs (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        date                    DATE UNIQUE,
        calories                REAL,
        protein_g               REAL,
        carbs_g                 REAL,
        fat_g                   REAL,
        fiber_g                 REAL,
        macrofactor_tdee        REAL,
        is_complete_log         INTEGER DEFAULT 1,  -- 0 = suspected incomplete day
        source                  TEXT DEFAULT 'macrofactor'
    );

    -- ----------------------------------------------------------------
    -- Weight logs (from MacroFactor or Apple Health)
    -- ----------------------------------------------------------------
    CREATE TABLE IF NOT EXISTS weight_logs (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        date    DATE,
        weight_kg REAL,
        source  TEXT DEFAULT 'macrofactor',
        UNIQUE(date, source)
    );

    -- ----------------------------------------------------------------
    -- Gym workouts (from Strong CSV)
    -- ----------------------------------------------------------------
    CREATE TABLE IF NOT EXISTS workouts (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        date             DATE,
        name             TEXT,
        duration_minutes INTEGER,
        notes            TEXT,
        source           TEXT DEFAULT 'strong'
    );

    CREATE TABLE IF NOT EXISTS workout_sets (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        workout_id    INTEGER REFERENCES workouts(id) ON DELETE CASCADE,
        exercise_name TEXT,
        set_number    INTEGER,
        weight_kg     REAL,
        reps          INTEGER,
        rpe           REAL,
        notes         TEXT
    );

    -- ----------------------------------------------------------------
    -- Daily health metrics (Apple Health or Garmin)
    -- ----------------------------------------------------------------
    CREATE TABLE IF NOT EXISTS daily_health (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        date              DATE UNIQUE,
        steps             INTEGER,
        active_calories   REAL,
        resting_calories  REAL,
        distance_km       REAL,
        floors_climbed    INTEGER,
        resting_heart_rate INTEGER,
        hrv               REAL,
        sleep_hours       REAL,
        source            TEXT
    );

    -- ----------------------------------------------------------------
    -- TDEE estimates (our adjusted estimates, recalculated weekly)
    -- ----------------------------------------------------------------
    CREATE TABLE IF NOT EXISTS tdee_estimates (
        id                     INTEGER PRIMARY KEY AUTOINCREMENT,
        week_starting          DATE UNIQUE,
        macrofactor_estimate   REAL,
        adjusted_estimate      REAL,
        logging_days           INTEGER,
        total_days             INTEGER,
        gap_correction_factor  REAL,
        notes                  TEXT,
        created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    conn.commit()
    conn.close()
    print("Database initialised at", DB_PATH)


if __name__ == "__main__":
    initialize()
