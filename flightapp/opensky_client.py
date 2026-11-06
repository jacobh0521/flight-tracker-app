import os
import time
import sys
import json
from typing import Dict, List, Optional, Union
from datetime import datetime, timezone

import requests
from .airports import get_airport_bbox_flow, get_airport_coordinates
from .models import get_cached_flow_states, save_cached_flow_states

# ----------------------------
# Config & OAuth2 Settings
# ----------------------------
OPEN_SKY_BASE = os.getenv("OPEN_SKY_BASE", "https://opensky-network.org").rstrip("/")
OPEN_SKY_TOKEN_URL = (
    "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
)

OPENSKY_CLIENT_ID = os.getenv("OPENSKY_CLIENT_ID")
OPENSKY_CLIENT_SECRET = os.getenv("OPENSKY_CLIENT_SECRET")
AVIATIONSTACK_KEY = os.getenv("AVIATIONSTACK_KEY")

OPEN_SKY_USERNAME = os.getenv("OPEN_SKY_USERNAME")
OPEN_SKY_PASSWORD = os.getenv("OPEN_SKY_PASSWORD")
BASIC_AUTH = (OPEN_SKY_USERNAME, OPEN_SKY_PASSWORD) if OPEN_SKY_USERNAME and OPEN_SKY_PASSWORD else None

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

CONNECT_TIMEOUT_SEC = 2.5
READ_TIMEOUT_SEC = 4.0

_ACCESS_TOKEN: Optional[str] = None
_TOKEN_EXPIRES_AT: float = 0.0
_OPENSKY_UNREACHABLE_UNTIL: float = 0.0

# ----------------------------
# Rate Limiting Configuration
# ----------------------------
_LAST_REQUEST_TIME = 0.0
MIN_INTERVAL_SEC = 1.0

DAILY_LIMIT = 4000
USAGE_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "usage_tracker.json"))


class UsageTracker:
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.count = 0
        self.last_ts = 0.0
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, "r") as f:
                    data = json.load(f)
                    if data.get("date") == self.today:
                        self.count = data.get("count", 0)
                        self.last_ts = data.get("last_ts", 0.0)
            except Exception:
                pass

    def _save(self) -> None:
        try:
            with open(self.file_path, "w") as f:
                json.dump(
                    {
                        "date": self.today,
                        "count": self.count,
                        "last_ts": self.last_ts,
                    },
                    f,
                )
        except Exception as e:
            print(f"--- USAGE TRACKER ERROR: {e}", file=sys.stderr)

    def increment(self) -> bool:
        if self.count >= DAILY_LIMIT:
            return False
        self.count += 1
        self._save()
        return True

    def update_timestamp(self, ts: float) -> None:
        self.last_ts = ts
        self._save()

    def get_count(self) -> int:
        return self.count

    def get_last_ts(self) -> float:
        return self.last_ts


tracker = UsageTracker(USAGE_FILE)
_LAST_REQUEST_TIME = tracker.get_last_ts()


def _get_oauth_token(force_refresh: bool = False) -> Optional[str]:
    global _ACCESS_TOKEN, _TOKEN_EXPIRES_AT, _OPENSKY_UNREACHABLE_UNTIL
    if not OPENSKY_CLIENT_ID or not OPENSKY_CLIENT_SECRET:
        return None

    now = time.time()
    if now < _OPENSKY_UNREACHABLE_UNTIL:
        return None

    if not force_refresh and _ACCESS_TOKEN and now < (_TOKEN_EXPIRES_AT - 60):
        return _ACCESS_TOKEN

    try:
        resp = requests.post(
            OPEN_SKY_TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": OPENSKY_CLIENT_ID,
                "client_secret": OPENSKY_CLIENT_SECRET,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=(CONNECT_TIMEOUT_SEC, READ_TIMEOUT_SEC),
        )
        resp.raise_for_status()
        token_data = resp.json()
        _ACCESS_TOKEN = token_data.get("access_token")
        expires_in = int(token_data.get("expires_in", 1800))
        _TOKEN_EXPIRES_AT = now + expires_in
        return _ACCESS_TOKEN
    except requests.exceptions.ConnectTimeout:
        _OPENSKY_UNREACHABLE_UNTIL = time.time() + 600
        return None
    except Exception:
        return None


def _make_request(
    endpoint: str, params: Dict, include_time: bool = False
) -> Optional[List[Union[Dict, List]]]:
    global _LAST_REQUEST_TIME, _OPENSKY_UNREACHABLE_UNTIL

    now = time.time()
    if now < _OPENSKY_UNREACHABLE_UNTIL:
        return None

    if include_time:
        params["time"] = int(now)

    elapsed = now - _LAST_REQUEST_TIME
    if 0 < elapsed < MIN_INTERVAL_SEC:
        time.sleep(MIN_INTERVAL_SEC - elapsed)

    if not tracker.increment():
        return None

    url = f"{OPEN_SKY_BASE}{endpoint}"
    _LAST_REQUEST_TIME = time.time()
    tracker.update_timestamp(_LAST_REQUEST_TIME)

    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    token = _get_oauth_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        response = requests.get(
            url,
            params=params,
            headers=headers,
            auth=BASIC_AUTH if not token else None,
            timeout=(CONNECT_TIMEOUT_SEC, READ_TIMEOUT_SEC),
        )
        if response.status_code == 404:
            return []
        response.raise_for_status()
        data = response.json()
        if endpoint == "/api/states/all":
            return data.get("states") or []
        return data
    except requests.exceptions.ConnectTimeout:
        _OPENSKY_UNREACHABLE_UNTIL = time.time() + 600
        return None
    except Exception:
        return None


def _fetch_aviationstack_departures(icao: str, begin: int, end: int) -> List[Dict]:
    """Fallback 1: Fetch real recent departures from AviationStack (skipping codeshare duplicates)."""
    if not AVIATIONSTACK_KEY:
        return []
    try:
        url = "http://api.aviationstack.com/v1/flights"
        params = {
            "access_key": AVIATIONSTACK_KEY,
            "dep_icao": icao.upper(),
            "limit": 50,
        }
        resp = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=4.0)
        resp.raise_for_status()
        payload = resp.json()
        flights = payload.get("data") or []

        results: List[Dict] = []
        seen_icao24 = set()

        for idx, f in enumerate(flights):
            # Skip codeshares to eliminate duplicate rows for the same physical plane
            if f.get("codeshared") is not None:
                continue

            flight_info = f.get("flight") or {}
            dep_info = f.get("departure") or {}
            arr_info = f.get("arrival") or {}
            aircraft_info = f.get("aircraft") or {}

            callsign = (
                flight_info.get("icao")
                or flight_info.get("number")
                or ""
            ).strip()
            if not callsign:
                continue

            icao24 = (aircraft_info.get("icao24") or f"as{idx:04x}").strip().lower()
            if icao24 in seen_icao24:
                continue
            seen_icao24.add(icao24)

            # ONLY use real reported timestamps from API. Never generate fake time.
            ts_str = (
                dep_info.get("actual")
                or dep_info.get("estimated")
                or dep_info.get("scheduled")
            )
            if not ts_str:
                continue

            try:
                dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                seen_ts = int(dt.timestamp())
                # Must be strictly within the requested lookback window
                if not (begin <= seen_ts <= end):
                    continue
            except Exception:
                continue

            results.append(
                {
                    "icao24": icao24,
                    "callsign": callsign,
                    "estDepartureAirport": icao.upper(),
                    "estArrivalAirport": (arr_info.get("icao") or "").upper() or None,
                    "firstSeen": seen_ts,
                    "lastSeen": seen_ts,
                }
            )
            if len(results) >= 20:
                break

        return results
    except Exception:
        return []


def get_airport_departures(icao: str, begin: int, end: int) -> List[Dict]:
    # 1. Try OpenSky live departures first
    result = _make_request("/api/flights/departure", {"airport": icao, "begin": begin, "end": end})
    if result:
        return result  # type: ignore

    # 2. Fallback to AviationStack (no synthetic fake timestamps)
    return _fetch_aviationstack_departures(icao, begin, end)


def get_airport_arrivals(icao: str, begin: int, end: int) -> List[Dict]:
    result = _make_request("/api/flights/arrival", {"airport": icao, "begin": begin, "end": end})
    return result if result is not None else []  # type: ignore


def get_flow_states(airport_icao: str) -> List[Dict]:
    # 1. Check fresh SQLite cache (15s TTL for real-time smoothness)
    cached_flows = get_cached_flow_states(airport_icao, max_age_sec=15)
    if cached_flows:
        return cached_flows

    coords = get_airport_coordinates(airport_icao)
    if not coords:
        return []

    lat, lon = coords
    flows: List[Dict] = []

    # 2. Try OpenSky bounding-box states first
    bbox = get_airport_bbox_flow(airport_icao)
    if bbox:
        lamin, lamax, lomin, lomax = bbox
        data = _make_request(
            "/api/states/all",
            {"lamin": lamin, "lamax": lamax, "lomin": lomin, "lomax": lomax},
        )
        if data:
            for s in data:
                try:
                    if s[6] is None or s[5] is None:
                        continue
                    flows.append(
                        {
                            "lat": float(s[6]),
                            "lon": float(s[5]),
                            "heading": s[10],
                            "velocity": s[9],
                            "icao24": str(s[0]).strip(),
                            "callsign": str(s[1] or "").strip(),
                            "source": "opensky",
                        }
                    )
                except Exception:
                    continue

    # 3. IF OpenSky is blocked/unreachable, use airplanes.live v2 point endpoint (never blocked by cloud IPs)
    if not flows:
        try:
            url = f"https://api.airplanes.live/v2/point/{lat}/{lon}/40"
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=4.0)
            resp.raise_for_status()
            payload = resp.json()
            ac_list = payload.get("ac") or []
            for ac in ac_list:
                lat_val = ac.get("lat")
                lon_val = ac.get("lon")
                if lat_val is None or lon_val is None:
                    continue
                flows.append(
                    {
                        "lat": float(lat_val),
                        "lon": float(lon_val),
                        "heading": ac.get("track"),
                        "velocity": ac.get("gs"),  # Ground speed in knots, map handles scaling
                        "icao24": str(ac.get("hex") or "").strip().lower(),
                        "callsign": str(ac.get("flight") or "").strip(),
                        "source": "airplanes.live",
                    }
                )
            if flows:
                print(f"--- AIRPLANES.LIVE FALLBACK: Loaded {len(flows)} live aircraft for {airport_icao}.", file=sys.stderr)
        except Exception as e:
            print(f"--- AIRPLANES.LIVE ERROR: {e}", file=sys.stderr)

    if flows:
        save_cached_flow_states(airport_icao, flows)
        return flows

    # 4. Final fallback to SQLite stale cache
    stale_cache = get_cached_flow_states(airport_icao, max_age_sec=86400 * 7)
    if stale_cache:
        return stale_cache

    return []