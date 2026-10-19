from math import radians, sin, cos, sqrt, atan2

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    earth = 6371.0
    p1, p2 = radians(lat1), radians(lat2)
    dp, dl = radians(lat2-lat1), radians(lon2-lon1)
    a = sin(dp/2)**2 + cos(p1)*cos(p2)*sin(dl/2)**2
    return 2 * earth * atan2(sqrt(a), sqrt(1-a))
