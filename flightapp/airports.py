# flightapp/airports.py
from typing import Optional, Tuple, Dict

# Minimal set 
_AIRPORTS = {
    # US Major airports (existing + added)
    "KATL": (33.6407, -84.4277),
    "KLAX": (33.9416, -118.4085),
    "KJFK": (40.6413, -73.7781),
    "KEWR": (40.6895, -74.1745),
    "KLGA": (40.7769, -73.8740),
    "KDCA": (38.8512, -77.0402),
    "KIAD": (38.9531, -77.4565),
    "KBOS": (42.3656, -71.0096),
    "KDEN": (39.8561, -104.6737),
    "KORD": (41.9742, -87.9073),
    "KDFW": (32.8998, -97.0403),
    "KSEA": (47.4502, -122.3088),
    "KSFO": (37.6213, -122.3790),
    "KPHX": (33.4373, -112.0081),
    "KBWI": (39.1754, -76.6684),
    "KSLC": (40.7899, -111.9791),
    "KMSP": (44.8848, -93.2223),
    "KDTW": (42.2162, -83.3554),
    "KPHL": (39.8744, -75.2424),
    "KSAN": (32.7338, -117.1933),
    "KTPA": (27.9755, -82.5332),
    "KAUS": (30.1975, -97.6664),
    "KLAS": (36.0840, -115.1537),
    "KRDU": (35.8776, -78.7875),
    "KBNA": (36.1317, -86.6689),
    "KSJC": (37.3639, -121.9289),
    # **Added requested airport**
    "KIND": (39.7173, -86.2944),  # Indianapolis International Airport:contentReference[oaicite:2]{index=2}

    # Other key U.S. airports (optional common hubs)
    "KMCO": (28.4312, -81.3081),  # Orlando International
    "KMIA": (25.7959, -80.2870),  # Miami International
    "KSEA": (47.4502, -122.3088), # Seattle–Tacoma Intl (already listed)
    "KCLT": (35.2140, -80.9431),  # Charlotte Douglas Intl
    "KMDW": (41.7858, -87.7522),  # Chicago Midway Intl
    "KIND": (39.7173, -86.2944),  # Indianapolis Intl (duplicate entry above)

    # Ecuador primary commercial airports (ICAO codes start with SE):contentReference[oaicite:3]{index=3}
    "SEQM": (-0.1298, -78.3587),  # Quito – Mariscal Sucre Intl
    "SEGU": (-2.1574, -79.8831),  # Guayaquil – José Joaquín de Olmedo Intl
    "SEMT": (-0.9475, -80.7333),  # Manta – Eloy Alfaro Intl
    "SELT": (-0.9060, -78.6150),  # Latacunga – Cotopaxi Intl
    "SEAM": (-1.2547, -78.6203),  # Ambato – Chachoán Airport
    "SECO": (-0.4690, -76.9867),  # Coca – Francisco de Orellana Airport
    "SESA": (-2.2124, -80.9603),  # Salinas – General Ulpiano Páez Airport
    "SETN": (1.2144, -79.6078),   # Esmeraldas – C. Concha Torres Airport
    "SEST": (-0.8975, -89.6017),  # San Cristóbal Airport
    "SERO": (-3.4740, -79.9697),  # Santa Rosa Airport
    "SEJD": (-0.9917, -77.8103),  # Tena – Jumandy Airport
    # Some smaller fields with IATA codes
    "SENL": (0.1083, -76.9319),   # Lago Agrio – Nueva Loja Airport
    "SEMC": (-2.3181, -78.1072),  # Macas – Edmundo Carvajal Airport
    "SECA": (-3.9994, -79.2047),  # Loja / La Toma Airport (Camilo Ponce Enriquez)
    "SECO": (-0.4690, -76.9867),  # Coca (duplicate above)
    "SEPV": (-1.0545, -80.4547),  # Portoviejo – Reales Tamarindos Airport
}
# Map some common IATA -> ICAO 
_IATA_TO_ICAO = {
    "ATL": "KATL","LAX":"KLAX","JFK":"KJFK","EWR":"KEWR","LGA":"KLGA",
    "DCA":"KDCA","IAD":"KIAD","BOS":"KBOS","DEN":"KDEN","ORD":"KORD",
    "DFW":"KDFW","SEA":"KSEA","SFO":"KSFO","PHX":"KPHX","BWI":"KBWI",
    "SLC":"KSLC","MSP":"KMSP","DTW":"KDTW","PHL":"KPHL","SAN":"KSAN",
    "TPA":"KTPA","AUS":"KAUS","LAS":"KLAS","RDU":"KRDU","BNA":"KBNA",
    "SJC":"KSJC"
}

# Bounding box calculation helper
def _get_bbox(airport: str, distance_deg: float) -> Optional[Tuple[float, float, float, float]]:
    coords = _AIRPORTS.get(airport.strip().upper())
    if coords is None:
        return None
    
    lat, lon = coords
    # [lat_min, lat_max, lon_min, lon_max]
    return (
        lat - distance_deg, 
        lat + distance_deg, 
        lon - distance_deg, 
        lon + distance_deg
    )

def get_airport_bbox_flow(airport: str) -> Optional[Tuple[float, float, float, float]]:
    """Get a moderately sized bounding box for the live flow map (approx 70km radius)."""
    return _get_bbox(airport, 0.6)


def get_airport_coordinates(airport_icao: str) -> Optional[Tuple[float, float]]:
    """Returns (lat, lon) for a given ICAO code."""
    icao = airport_icao.strip().upper()
    return _AIRPORTS.get(icao)

def get_icao_for_iata(iata_code: str) -> Optional[str]:
    """Returns ICAO code for a given IATA code (e.g., ATL -> KATL)."""
    iata = iata_code.strip().upper()
    return _IATA_TO_ICAO.get(iata)