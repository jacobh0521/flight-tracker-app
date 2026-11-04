import os
import time
import sys
import json
from typing import Dict, List, Optional, Union
from datetime import datetime, timezone

import requests
from .airports import get_airport_bbox_flow
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

OPEN_SKY_USERNAME = os.getenv("OPEN_SKY_USERNAME")
OPEN_SKY_PASSWORD = os.getenv("OPEN_SKY_PASSWORD")
BASIC_AUTH = (OPEN_SKY_USERNAME, OPEN_SKY_PASSWORD) if OPEN_SKY_USERNAME and OPEN_SKY_PASSWORD else None

USER_AGENT = "FlightApp/1.0"
TIMEOUT_SEC = float(os.getenv("OPEN_SKY_TIMEOUT_SEC", "15.0"))

_ACCESS_TOKEN: Optional[str] = None
_TOKEN_EXPIRES_AT: float = 0.0

# ----------------------------
# Rate Limiting Configuration
# ----------------------------
try:
    MIN_INTERVAL_SEC = float(os.getenv("OPENSKY_MIN_INTERVAL", "10.0"))
except ValueError:
    MIN_INTERVAL_SEC = 10.0

_LAST_REQUEST_TIME = 0.0

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
                with open(self.file_path, 'r') as f:
                    data = json.load(f)
                    if data.get("date") == self.today:
                        self.count = data.get("count", 0)
                        self.last_ts = data.get("last_ts", 0.0)
            except Exception:
                pass

    def _save(self) -> None:
        try:
            with open(self.file_path, 'w') as f:
                json.dump({
                    "date": self.today,
                    "count": self.count,
                    "last_ts": self.last_ts
                }, f)
        except Exception as e:
            print(f"--- USAGE TRACKER ERROR: Could not save usage file: {e}", file=sys.stderr)

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
    global _ACCESS_TOKEN, _TOKEN_EXPIRES_AT
    if not OPENSKY_CLIENT_ID or not OPENSKY_CLIENT_SECRET:
        return None

    now = time.time()
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
            timeout=5.0,
        )
        resp.raise_for_status()
        token_data = resp.json()
        _ACCESS_TOKEN = token_data.get("access_token")
        expires_in = int(token_data.get("expires_in", 1800))
        _TOKEN_EXPIRES_AT = now + expires_in
        return _ACCESS_TOKEN
    except Exception:
        return None


def _make_request(endpoint: str, params: Dict, include_time: bool = False) -> Optional[List[List[Union[str, int, float]]]]:
    global _LAST_REQUEST_TIME
    if include_time:
        params['time'] = int(time.time())

    now = time.time()
    elapsed = now - _LAST_REQUEST_TIME
    if elapsed < MIN_INTERVAL_SEC:
        time.sleep(MIN_INTERVAL_SEC - elapsed)

    if not tracker.increment():
        return None

    url = f"{OPEN_SKY_BASE}{endpoint}"

    for attempt in range(2):
        _LAST_REQUEST_TIME = time.time()
        tracker.update_timestamp(_LAST_REQUEST_TIME)

        headers = {"User-Agent": USER_AGENT}
        token = _get_oauth_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            # Use a strict 3-second timeout on Cloud Run to prevent hanging
            response = requests.get(url, params=params, headers=headers, timeout=TIMEOUT_SEC)
            if response.status_code == 401 and attempt == 0:
                _get_oauth_token(force_refresh=True)
                continue
            if response.status_code == 404:
                return []
            response.raise_for_status()
            data = response.json()
            if endpoint == "/api/states/all":
                return data.get("states") or []
            return data
        except Exception:
            break
    return None


def get_airport_departures(icao: str, begin: int, end: int) -> List[List[Union[str, int, float]]]:
    # Try fetching live, but if it fails or times out instantly, return empty so routes.py uses DB cache
    result = _make_request("/api/flights/departure", {"airport": icao, "begin": begin, "end": end})
    return result if result is not None else []


def get_airport_arrivals(icao: str, begin: int, end: int) -> List[List[Union[str, int, float]]]:
    result = _make_request("/api/flights/arrival", {"airport": icao, "begin": begin, "end": end})
    return result if result is not None else []


def get_flow_states(airport_icao: str) -> List[Dict]:
    # 1. INSTANT CACHE FIRST: Serve SQLite cache immediately if available (up to 24h old)
    cached_flows = get_cached_flow_states(airport_icao, max_age_sec=86400)
    if cached_flows:
        print(f"--- FLOW INSTANT CACHE HIT: Serving {airport_icao} from SQLite.", file=sys.stderr)
        return cached_flows

    # 2. Only query OpenSky if cache was completely empty
    bbox = get_airport_bbox_flow(airport_icao)
    if not bbox:
        return []

    lamin, lamax, lomin, lomax = bbox
    data = _make_request("/api/states/all", {"lamin": lamin, "lamax": lamax, "lomin": lomin, "lomax": lomax})
    
    flows: List[Dict] = []
    if data:
        for s in data:
            try:
                flows.append({
                    "lat": float(s[6]),
                    "lon": float(s[5]),
                    "heading": s[10],
                    "velocity": s[9],
                    "icao24": str(s[0]).strip(),
                    "callsign": str(s[1]).strip(),
                    "source": "opensky",
                })
            except Exception:
                continue

    if flows:
        save_cached_flow_states(airport_icao, flows)
        return flows

    return []