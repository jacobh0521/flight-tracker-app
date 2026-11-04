import os
import sqlite3
import json
from typing import Iterable, List, Tuple, Optional, Dict 
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import time
import sys

# ---------- time formatting ----------
def utc_to_est(epoch_str: str) -> str:
    try:
        epoch = int(epoch_str)
    except (TypeError, ValueError):
        return epoch_str or "-"
    utc_dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
    local_dt = utc_dt.astimezone(ZoneInfo("America/New_York"))
    return local_dt.strftime("%Y-%m-%d %H:%M:%S %Z")

# DB path alongside project root
DB_NAME = os.getenv("DB_PATH", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "flights.db")))


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(DB_NAME)

def _column_names(cur) -> List[str]:
    cur.execute("PRAGMA table_info(flights)")
    return [row[1] for row in cur.fetchall()]

def init_db() -> None:
    conn = _connect()
    cur = conn.cursor()

    # 1. Base table creation (ensures table exists)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS flights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            icao24 TEXT NOT NULL,
            callsign TEXT,
            departure_airport TEXT,
            arrival_airport TEXT,
            departure_time INTEGER, -- The timestamp reported by OpenSky/ADS-B
            delay_minutes INTEGER,
            flight_kind TEXT NOT NULL, -- 'DEP' or 'ARR'
            event_time INTEGER NOT NULL, -- The time this record was saved
            UNIQUE(icao24, event_time, flight_kind) ON CONFLICT REPLACE
        )
        """
    )
    
    # --- Flow Cache Table for Persistent Map Caching ---
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS flow_cache (
            airport TEXT PRIMARY KEY,
            states_json TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )

    # 2. Add Index for faster lookup by event_time and airport
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_flight_search ON flights (
            event_time DESC, 
            departure_airport, 
            arrival_airport
        )
        """
    )
    conn.commit()
    conn.close()


def get_cached_flow_states(airport: str, max_age_sec: int = 86400) -> Optional[List[Dict]]:
    """Retrieves cached live flight states from SQLite."""
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT states_json, updated_at FROM flow_cache WHERE airport = ?",
        (airport.upper(),)
    )
    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    states_json, updated_at = row
    now = int(time.time())

    if (now - updated_at) <= max_age_sec:
        try:
            return json.loads(states_json)
        except Exception:
            return None
    return None


def save_cached_flow_states(airport: str, states: List[Dict]) -> None:
    """Saves live flight states snapshot to SQLite cache."""
    conn = _connect()
    cur = conn.cursor()
    now = int(time.time())
    try:
        states_json = json.dumps(states)
        cur.execute(
            """
            INSERT INTO flow_cache (airport, states_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(airport) DO UPDATE SET
                states_json = excluded.states_json,
                updated_at = excluded.updated_at
            """,
            (airport.upper(), states_json, now)
        )
        conn.commit()
    except Exception as e:
        print(f"--- FLOW CACHE DB ERROR: {e}", file=sys.stderr)
    finally:
        conn.close()


def get_flights_for_last_search(
    airport: str,
    minutes_back: int,
    arrival_lag: int,
    arrival_buffer: int
) -> Tuple[List[Tuple], List[Tuple]]:
    conn = _connect()
    cur = conn.cursor()

    now = int(time.time())
    lookback_sec = minutes_back * 60

    dep_end = now
    dep_begin = now - lookback_sec

    arr_end = now + arrival_buffer
    arr_begin = now - lookback_sec - arrival_lag

    cur.execute(
        """
        SELECT 
            icao24, callsign, departure_airport, arrival_airport,
            departure_time, delay_minutes, flight_kind, event_time
        FROM flights
        WHERE (
            flight_kind = 'DEP'
            AND UPPER(COALESCE(departure_airport, '')) = UPPER(?)
            AND CAST(COALESCE(event_time, departure_time) AS INTEGER) BETWEEN ? AND ?
        )
        OR (
            flight_kind = 'ARR'
            AND UPPER(COALESCE(arrival_airport, '')) = UPPER(?)
            AND CAST(COALESCE(event_time, departure_time) AS INTEGER) BETWEEN ? AND ?
        )
        ORDER BY event_time DESC
        """,
        (airport, dep_begin, dep_end, airport, arr_begin, arr_end),
    )
    rows = cur.fetchall()
    conn.close()

    summary_rows = [row for row in rows if row[1] is None] 
    return rows, summary_rows


def get_destination_by_icao24(icao24: str) -> Optional[Dict]:
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT arrival_airport
        FROM flights
        WHERE icao24 = ? 
        AND arrival_airport IS NOT NULL 
        ORDER BY event_time DESC
        LIMIT 1
        """,
        (icao24,),
    )
    row = cur.fetchone()
    conn.close()
    return {"arrival_airport": row[0]} if row else None


def add_flight(
    icao24: str,
    callsign: Optional[str],
    dep_airport: Optional[str],
    arr_airport: Optional[str],
    dep_time: str,
    delay_minutes: Optional[int],
    flight_kind: str,
    event_time: str
) -> None:
    conn = _connect()
    cur = conn.cursor()
    try:
        dep_time_int = int(dep_time)
        event_time_int = int(event_time)
    except (TypeError, ValueError):
        print(f"ERROR: Invalid time format for flight {icao24}. Skipping.", file=sys.stderr)
        conn.close()
        return

    cur.execute(
        "SELECT id FROM flights WHERE icao24 = ? AND event_time = ? AND flight_kind = ?",
        (icao24, event_time_int, flight_kind)
    )
    existing_row = cur.fetchone()
    if existing_row:
        if callsign:
            cur.execute("UPDATE flights SET callsign = ? WHERE id = ?", (callsign, existing_row[0]))
            conn.commit()
        conn.close()
        return

    cur.execute(
        """
        INSERT INTO flights (
            icao24, callsign, departure_airport, arrival_airport,
            departure_time, delay_minutes, flight_kind, event_time
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            icao24,
            callsign,
            dep_airport,
            arr_airport,
            dep_time_int,
            delay_minutes,
            flight_kind,
            event_time_int,
        ),
    )
    conn.commit()
    conn.close()


def get_icao24_types() -> Dict[str, str]:
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT icao24, callsign
        FROM flights
        WHERE callsign IS NOT NULL AND callsign != ''
        GROUP BY icao24
        HAVING MAX(event_time)
        """
    )
    rows = cur.fetchall()
    conn.close()
    
    icao24_types = {}
    for icao24, callsign in rows:
        type_code = (callsign or "").strip().upper()
        if type_code:
            icao24_types[icao24] = type_code[:3]

    return icao24_types


def update_flight_callsigns(flight_list: List[Dict]):
    conn = _connect()
    cur = conn.cursor()

    updates = 0
    for flight in flight_list:
        icao24 = flight.get("icao24")
        new_callsign = flight.get("callsign")
        new_dep_time = flight.get("firstSeen")
        new_event_time = flight.get("lastSeen")

        if icao24 and new_callsign:
            cur.execute(
                """
                UPDATE flights
                SET 
                    callsign = ?,
                    departure_time = COALESCE(departure_time, ?),
                    event_time = ?
                WHERE icao24 = ? AND callsign IS NULL
                """,
                (new_callsign, new_dep_time, new_event_time, icao24)
            )
            updates += cur.rowcount
    
    conn.commit()
    conn.close()
    return updates