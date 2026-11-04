import os
import time
import sys

from flask import Blueprint, render_template, jsonify, request, session

from .models import (
    get_flights_for_last_search,
    add_flight,
)

from .opensky_client import (
    get_airport_departures,
    get_airport_arrivals,
    get_flow_states
)
from .airports import get_airport_coordinates

bp = Blueprint("main", __name__)

def _now() -> int:
    return int(time.time())

def _to_icao(code: str) -> str:
    code = (code or "").strip().upper()
    if len(code) == 3 and not code.startswith("K"):
        return "K" + code
    return code

@bp.route("/")
def index():
    default_airport = os.getenv("DEFAULT_AIRPORT", "KDCA")
    airport = session.get("last_airport", default_airport)
    minutes_back = int(session.get("minutes_back", 30))

    try:
        flights_raw, summary_rows = get_flights_for_last_search(
            airport=_to_icao(airport),
            minutes_back=minutes_back,
            arrival_lag=0,
            arrival_buffer=0
        )
        flights_raw = [r for r in flights_raw if r[6] == "DEP"]

        enriched_flights = []
        biz_prefixes = {"EJA", "EJM", "LXJ", "FXC", "VJA", "JAS", "TCN", "XOJ", "GJE", "PDC"}

        for r in flights_raw:
            callsign = r[1]
            cat = "Unknown"
            if callsign:
                c = callsign.strip().upper()
                if c.startswith("N") and len(c) > 1 and c[1].isdigit():
                    cat = "General Aviation"
                elif len(c) >= 3 and c[:3] in biz_prefixes:
                    cat = "Business / Charter"
                elif len(c) >= 4 and c[:3].isalpha() and c[3].isdigit():
                    cat = "Commercial"
            enriched_flights.append(r + (cat,))
        flights_raw = enriched_flights
    except Exception as e:
        print(f"DB ERROR (index): {e}", file=sys.stderr)
        flights_raw = []

    dep_rows = [r for r in flights_raw if r[6] == "DEP"]

    def compute_delay(rows):
        vals = [r[5] for r in rows if r[5] is not None]
        return {"min": 0, "max": 0, "avg": 0} if not vals else {"min": min(vals), "max": max(vals), "avg": round(sum(vals)/len(vals),2)}

    def compute_spacing(rows):
        times = [int(r[7]) for r in rows if r[7] is not None]
        times.sort()
        if len(times) < 2:
            return {"min":0, "max":0, "avg":0}
        deltas = [b-a for a,b in zip(times[:-1], times[1:])]
        return {"min": round(min(deltas)/60,2), "max": round(max(deltas)/60,2), "avg": round(sum(deltas)/len(deltas)/60,2)}

    metric_data = {
        "last_search_info": {"total_flights": len(flights_raw), "minutes_back": minutes_back, "include_arrivals": False},
        "dep_metrics": {"delay": compute_delay(dep_rows), "spacing": compute_spacing(dep_rows)},
    }

    return render_template("index.html", airport=airport, minutes_back=minutes_back, include_arrivals=False, flights=flights_raw, metric_data=metric_data)

@bp.route("/api/fetch", methods=['GET', 'POST'])
def api_fetch():
    t0 = _now()
    data = request.args if request.method == 'GET' else request.get_json(force=True)

    airport = (data.get("airport") or "").strip().upper()
    minutes_back = int(data.get("minutes_back", 30))
    if len(airport) == 3:
        airport = "K" + airport

    now_ts = _now()

    # --- INSTANT SERVER-SIDE CACHE CHECK (TTL: 30 minutes) ---
    existing_flights, _ = get_flights_for_last_search(
        airport=airport, 
        minutes_back=minutes_back, 
        arrival_lag=0, 
        arrival_buffer=0
    )
    
    if existing_flights:
        newest_event_time = max([int(r[7]) for r in existing_flights if r[7] is not None], default=0)
        if (now_ts - newest_event_time) < 1800:
            print(f"--- INSTANT CACHE HIT: Serving {airport} from SQLite in <0.1s.", file=sys.stderr)
            session["last_airport"] = airport
            session["minutes_back"] = minutes_back
            return jsonify({
                "ok": True,
                "airport": airport,
                "saved_dep": len([r for r in existing_flights if r[6] == "DEP"]),
                "saved_arr": len([r for r in existing_flights if r[6] == "ARR"]),
                "elapsed": _now() - t0,
                "cached": True
            })

    # If cache is old/empty, try fetching live
    dep_begin = now_ts - (minutes_back * 60)
    dep_rows = get_airport_departures(airport, dep_begin, now_ts)
    
    saved_dep = 0
    for d in dep_rows:
        add_flight(
            icao24=d.get("icao24"),
            callsign=d.get("callsign"),
            dep_airport=d.get("estDepartureAirport"),
            arr_airport=d.get("estArrivalAirport"),
            dep_time=str(d.get("firstSeen")),
            delay_minutes=None,
            flight_kind="DEP",
            event_time=str(d.get("firstSeen")),
        )
        saved_dep += 1

    # Refresh existing flights reference after saving live rows
    if saved_dep == 0:
        existing_flights, _ = get_flights_for_last_search(
            airport=airport, 
            minutes_back=minutes_back, 
            arrival_lag=0, 
            arrival_buffer=0
        )
        saved_dep = len([r for r in existing_flights if r[6] == "DEP"])

    session["last_airport"] = airport
    session["minutes_back"] = minutes_back

    return jsonify({"ok": True, "airport": airport, "saved_dep": saved_dep, "saved_arr": 0, "elapsed": _now() - t0, "cached": False})

@bp.route("/api/flow")
def api_flow():
    airport = (request.args.get("airport") or "KJFK").strip().upper()
    coords = get_airport_coordinates(airport)
    flows = get_flow_states(airport)
    return jsonify({
        "states": flows,
        "center": {"lat": coords[0], "lon": coords[1]} if coords else None
    })

@bp.route("/flow")
def flow_map():
    default_airport = os.getenv("DEFAULT_AIRPORT","KDCA")
    airport = _to_icao(request.args.get("airport", default_airport))
    coords = get_airport_coordinates(airport) or (38.8512, -77.0402)
    return render_template("flow.html", airport=airport, icao24_list="", initial_lat=coords[0], initial_lon=coords[1])

@bp.route("/about")
def about():
    return render_template("about.html")