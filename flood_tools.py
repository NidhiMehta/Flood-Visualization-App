# -*- coding: utf-8 -*-
"""Tool functions for fetching and visualizing flood data for any US region.

Same data logic as the AgentScope version; adapted for LangChain:
- @tool decorator instead of ToolResponse/TextBlock
- Functions return plain strings
"""
import json
import os
import ssl
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

import certifi
from langchain.tools import tool

_STATE_CODES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY",
}

_OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
_CACHE_DIR = os.path.join(_OUTPUT_DIR, "cache")
_DALLAS_CENTER = (32.7767, -96.7970)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _save_cache(key: str, payload: object) -> None:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    entry = {"fetched_at": datetime.now(timezone.utc).isoformat(), "data": payload}
    with open(os.path.join(_CACHE_DIR, f"{key}.json"), "w", encoding="utf-8") as f:
        json.dump(entry, f, ensure_ascii=False, indent=2)


def _load_cache(key: str) -> tuple:
    path = os.path.join(_CACHE_DIR, f"{key}.json")
    if not os.path.exists(path):
        return None, None
    try:
        with open(path, encoding="utf-8") as f:
            entry = json.load(f)
        return entry["data"], entry["fetched_at"]
    except Exception:
        return None, None


def _http_get(url: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "FloodWatchAgent/1.0 (flood research; nidhi_mehta@outlook.com)",
            "Accept": "application/json",
        },
    )
    ctx = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read().decode())


def _geocode(location_name: str) -> dict | None:
    cache_key = "geocode_" + location_name.lower().replace(" ", "_").replace(",", "")
    cached, _ = _load_cache(cache_key)
    if cached:
        return cached

    url = (
        "https://nominatim.openstreetmap.org/search"
        f"?q={urllib.parse.quote(location_name)}"
        "&format=json&limit=1&addressdetails=1&countrycodes=us"
    )
    try:
        results = _http_get(url)
    except Exception:
        return None

    if not results:
        return None

    r = results[0]
    lat, lon = float(r["lat"]), float(r["lon"])

    bb = r.get("boundingbox", [])
    if bb:
        s, n, w, e = float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])
        pad = max(0.0, 0.5 - (n - s) / 2), max(0.0, 0.5 - (e - w) / 2)
        bbox = (
            round(w - pad[1], 4), round(s - pad[0], 4),
            round(e + pad[1], 4), round(n + pad[0], 4),
        )
    else:
        bbox = (round(lon - 0.5, 4), round(lat - 0.5, 4),
                round(lon + 0.5, 4), round(lat + 0.5, 4))

    state_code = None
    try:
        nws = _http_get(f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}")
        state_code = (
            nws.get("properties", {})
            .get("relativeLocation", {})
            .get("properties", {})
            .get("state")
        )
    except Exception:
        pass

    if not state_code:
        state_name = r.get("address", {}).get("state", "")
        state_code = _STATE_CODES.get(state_name)

    if not state_code:
        return None

    info = {
        "lat": lat, "lon": lon, "bbox": bbox,
        "display_name": r.get("display_name", location_name).split(",")[0].strip(),
        "state_code": state_code,
    }
    _save_cache(cache_key, info)
    return info


def _generate_map(
    locations: list,
    title: str = "Flood Visualization",
    output_filename: str = "flood_map.html",
    center: tuple = _DALLAS_CENTER,
) -> str:
    """Write a Leaflet.js HTML map and return its path."""
    if not locations:
        return "No locations provided — map not generated."

    output_path = os.path.join(_OUTPUT_DIR, output_filename)
    color_map = {
        "warning": "#e74c3c",
        "watch": "#e67e22",
        "gauge": "#3498db",
        "historical": "#8e44ad",
        "info": "#27ae60",
    }

    def _radius(loc: dict) -> int:
        mtype = loc.get("marker_type", "")
        if mtype == "warning":
            return 16
        if mtype == "watch":
            return 12
        return 7

    marker_parts = []
    for loc in locations:
        lat = float(loc.get("lat", center[0]))
        lon = float(loc.get("lon", center[1]))
        t = str(loc.get("title", "")).replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ")
        d = str(loc.get("description", "")).replace("\\", "\\\\").replace("'", "\\'").replace("\n", "<br>")[:400]
        mtype = str(loc.get("marker_type", "info"))
        color = color_map.get(mtype, color_map["info"])
        r = _radius(loc)
        marker_parts.append(
            f"    L.circleMarker([{lat}, {lon}], {{"
            f"radius:{r},fillColor:'{color}',color:'#fff',weight:2,opacity:1,fillOpacity:0.85"
            f"}}).addTo(map).bindPopup('<b>{t}</b><br><small>{d}</small>');"
        )

    markers_js = "\n".join(marker_parts)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    html = (
        "<!DOCTYPE html>\n<html lang='en'>\n<head>\n"
        "<meta charset='utf-8'/>\n"
        "<meta name='viewport' content='width=device-width, initial-scale=1.0'/>\n"
        f"<title>{title}</title>\n"
        "<link rel='stylesheet' href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css' crossorigin=''/>\n"
        "<script src='https://unpkg.com/leaflet@1.9.4/dist/leaflet.js' crossorigin=''></script>\n"
        "<style>\n"
        "  *{box-sizing:border-box;margin:0;padding:0}\n"
        "  body{font-family:Arial,sans-serif;display:flex;flex-direction:column;height:100vh}\n"
        "  header{background:#1a3a5c;color:#fff;padding:10px 16px;flex-shrink:0}\n"
        "  header h1{font-size:1.2rem;margin-bottom:2px}\n"
        "  header small{font-size:.75rem;opacity:.8}\n"
        "  #map{flex:1}\n"
        "  .legend{background:#fff;padding:10px 14px;border-radius:6px;"
        "box-shadow:0 1px 5px rgba(0,0,0,.3);font-size:13px;line-height:1.9}\n"
        "  .legend i{display:inline-block;width:14px;height:14px;border-radius:50%;"
        "margin-right:6px;vertical-align:middle}\n"
        "</style>\n</head>\n<body>\n"
        "<header>\n"
        f"  <h1>&#128167; {title}</h1>\n"
        f"  <small>Generated: {now} &middot; {len(locations)} markers</small>\n"
        "</header>\n"
        "<div id='map'></div>\n"
        "<script>\n"
        f"  const map = L.map('map').setView([{center[0]},{center[1]}],10);\n"
        "  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{\n"
        "    maxZoom:19,\n"
        "    attribution:'&copy; <a href=\"https://www.openstreetmap.org/copyright\">OpenStreetMap</a>'\n"
        "  }).addTo(map);\n"
        + markers_js + "\n"
        "  const legend = L.control({position:'bottomright'});\n"
        "  legend.onAdd = function() {\n"
        "    const d = L.DomUtil.create('div','legend');\n"
        "    d.innerHTML = '<b>Legend</b><br>'\n"
        "      + '<i style=\"background:#e74c3c\"></i>Flood Warning<br>'\n"
        "      + '<i style=\"background:#e67e22\"></i>Flood Watch / Advisory<br>'\n"
        "      + '<i style=\"background:#3498db\"></i>Stream Gauge (live)<br>'\n"
        "      + '<i style=\"background:#8e44ad\"></i>Historical Peak<br>'\n"
        "      + '<i style=\"background:#27ae60\"></i>Info';\n"
        "    return d;\n"
        "  };\n"
        "  legend.addTo(map);\n"
        "</script>\n</body>\n</html>\n"
    )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    return f"Map saved: {output_path}\n{len(locations)} markers plotted."


def _open_map(filename: str = "flood_map.html") -> None:
    if os.environ.get("FLOODWATCH_WEB_MODE"):
        return
    path = os.path.join(_OUTPUT_DIR, filename)
    if not os.path.exists(path):
        return
    if sys.platform == "darwin":
        subprocess.Popen(["open", path])
    elif sys.platform.startswith("linux"):
        subprocess.Popen(["xdg-open", path])
    else:
        os.startfile(path)  # type: ignore[attr-defined]


def _write_map(locations: list, title: str, center: tuple = _DALLAS_CENTER) -> None:
    _generate_map(locations, title=title, center=center)
    _open_map()


# ---------------------------------------------------------------------------
# LangChain tools
# ---------------------------------------------------------------------------

@tool
def show_current_flood_map(location: str = "Dallas, TX") -> str:
    """Fetch live NWS flood alerts and USGS stream gauge readings for any US city or
    region, then generate and display an interactive map in one step.

    Use this whenever the user asks about current flood conditions, active warnings,
    or wants to see a live flood map for any location.

    Args:
        location: US city or region, e.g. "Houston, TX", "New Orleans, LA". Default "Dallas, TX".
    """
    geo = _geocode(location)
    if not geo:
        return f"Could not geocode '{location}'. Try a more specific US city name."

    center = (geo["lat"], geo["lon"])
    west, south, east, north = geo["bbox"]
    state_code = geo["state_code"]
    display = geo["display_name"]
    cache_key_nws = f"nws_alerts_{state_code}_{display.lower().replace(' ', '_')}"
    cache_key_usgs = f"usgs_gauges_{display.lower().replace(' ', '_')}"

    map_locations = []
    summary_parts = []

    # NWS alerts filtered to bbox
    try:
        data = _http_get(f"https://api.weather.gov/alerts/active?area={state_code}")
        alerts = []
        for feature in data.get("features", []):
            props = feature.get("properties", {})
            if "flood" not in props.get("event", "").lower():
                continue
            lat, lon = center
            geom = feature.get("geometry")
            if geom:
                gtype = geom.get("type", "")
                if gtype == "Polygon":
                    coords = geom["coordinates"][0]
                    lon = sum(c[0] for c in coords) / len(coords)
                    lat = sum(c[1] for c in coords) / len(coords)
                elif gtype == "MultiPolygon":
                    all_c = [c for p in geom["coordinates"] for r in p for c in r]
                    lon = sum(c[0] for c in all_c) / len(all_c)
                    lat = sum(c[1] for c in all_c) / len(all_c)
            if not (west <= lon <= east and south <= lat <= north):
                continue
            event = props.get("event", "Flood Alert")
            mtype = "warning" if "warning" in event.lower() else "watch"
            alerts.append({
                "lat": round(lat, 5), "lon": round(lon, 5),
                "title": event + " — " + props.get("areaDesc", "")[:50],
                "description": f"Severity: {props.get('severity', '')}. Expires: {props.get('expires', '')[:16]}",
                "marker_type": mtype,
            })
        _save_cache(cache_key_nws, alerts)
        map_locations.extend(alerts)
        summary_parts.append(f"{len(alerts)} NWS flood alert(s)")
    except Exception:
        cached, fetched_at = _load_cache(cache_key_nws)
        if cached:
            map_locations.extend(cached)
            summary_parts.append(f"{len(cached)} cached NWS alerts (from {fetched_at[:10]})")

    # USGS live gauges
    try:
        data = _http_get(
            "https://waterservices.usgs.gov/nwis/iv/?format=json"
            f"&bBox={west},{south},{east},{north}"
            "&parameterCd=00060,00065&siteStatus=active&siteType=ST"
        )
        stations: dict = {}
        for ts in data.get("value", {}).get("timeSeries", []):
            si = ts.get("sourceInfo", {})
            code = (si.get("siteCode") or [{}])[0].get("value", "")
            geo_loc = si.get("geoLocation", {}).get("geogLocation", {})
            lat, lon = geo_loc.get("latitude"), geo_loc.get("longitude")
            if not lat or not lon:
                continue
            param_code = (ts.get("variable", {}).get("variableCode") or [{}])[0].get("value", "")
            param = ts.get("variable", {}).get("variableName", "")
            unit = ts.get("variable", {}).get("unit", {}).get("unitCode", "")
            vals = (ts.get("values") or [{}])[0].get("value", [])
            if not vals:
                continue
            v = vals[-1]
            if not v.get("value") or v["value"] == "-999999":
                continue
            reading_time = v.get("dateTime", "")[:16].replace("T", " ")
            if code not in stations:
                stations[code] = {"name": si.get("siteName", ""), "lat": lat, "lon": lon,
                                  "readings": [], "gage_height": None, "gage_unit": "",
                                  "reading_time": reading_time}
            stations[code]["readings"].append(f"{v['value']} {unit} ({param})")
            if param_code == "00065":
                stations[code]["gage_height"] = float(v["value"])
                stations[code]["gage_unit"] = unit
                stations[code]["reading_time"] = reading_time
        gauges = list(stations.values())
        _save_cache(cache_key_usgs, gauges)
        for g in gauges:
            desc_parts = g["readings"][:2]
            if g.get("reading_time"):
                desc_parts = desc_parts + [f"As of {g['reading_time']}"]
            map_locations.append({
                "lat": g["lat"], "lon": g["lon"],
                "title": g["name"],
                "description": " | ".join(desc_parts),
                "marker_type": "gauge",
                "peak_value": g.get("gage_height"),
                "peak_unit": g.get("gage_unit", "ft"),
            })
        summary_parts.append(f"{len(gauges)} USGS gauge(s)")
    except Exception:
        cached, fetched_at = _load_cache(cache_key_usgs)
        if cached:
            for g in cached:
                map_locations.append({
                    "lat": g["lat"], "lon": g["lon"],
                    "title": g["name"],
                    "description": "Cached from " + (fetched_at or "")[:10],
                    "marker_type": "gauge",
                })
            summary_parts.append(f"{len(cached)} cached USGS gauges")

    if not map_locations:
        return f"No flood data found for {display}."

    _write_map(map_locations, f"{display} — Live Flood Conditions", center=center)
    return f"Map generated for {display}: " + ", ".join(summary_parts) + "."


@tool
def show_historical_flood_map(location: str = "Dallas, TX", days_back: int = 7) -> str:
    """Fetch USGS daily peak stream gauge readings for any US city or region over
    the past N days, then generate and display an interactive map.

    Use this whenever the user asks about past flooding, last week, recent surges,
    or historical water levels for any location.

    Args:
        location: US city or region, e.g. "Houston, TX", "Nashville, TN". Default "Dallas, TX".
        days_back: Number of past days to include (1-30). Default 7.
    """
    geo = _geocode(location)
    if not geo:
        return f"Could not geocode '{location}'. Try a more specific US city name."

    west, south, east, north = geo["bbox"]
    display = geo["display_name"]
    days_back = max(1, min(30, days_back))
    end_dt = date.today()
    start_dt = end_dt - timedelta(days=days_back)
    cache_key = f"usgs_hist_{display.lower().replace(' ', '_')}_{days_back}d"

    url = (
        "https://waterservices.usgs.gov/nwis/dv/?format=json"
        f"&bBox={west},{south},{east},{north}"
        "&parameterCd=00060,00065"
        f"&startDT={start_dt}&endDT={end_dt}"
        "&siteType=ST&siteStatus=active&statCd=00003"
    )

    stations: dict = {}
    data_source = "live"
    try:
        data = _http_get(url)
        for ts in data.get("value", {}).get("timeSeries", []):
            si = ts.get("sourceInfo", {})
            code = (si.get("siteCode") or [{}])[0].get("value", "")
            geo_loc = si.get("geoLocation", {}).get("geogLocation", {})
            lat, lon = geo_loc.get("latitude"), geo_loc.get("longitude")
            if not lat or not lon:
                continue
            param_code = (ts.get("variable", {}).get("variableCode") or [{}])[0].get("value", "")
            unit = ts.get("variable", {}).get("unit", {}).get("unitCode", "")
            vals = [v for v in (ts.get("values") or [{}])[0].get("value", [])
                    if v.get("value") and v["value"] != "-999999"]
            if not vals:
                continue
            peak = max(vals, key=lambda v: float(v["value"]))
            if code not in stations:
                stations[code] = {"name": si.get("siteName", ""), "lat": lat, "lon": lon, "peaks": []}
            stations[code]["peaks"].append({
                "param_code": param_code,
                "peak_value": float(peak["value"]), "unit": unit,
                "peak_date": peak["dateTime"][:10],
            })
        result = sorted(
            stations.values(),
            key=lambda s: next((p["peak_value"] for p in s["peaks"] if p["param_code"] == "00065"), 0),
            reverse=True,
        )
        _save_cache(cache_key, result)
    except Exception as exc:
        cached, fetched_at = _load_cache(cache_key)
        if cached:
            result = cached
            data_source = f"cache from {fetched_at[:10]}"
        else:
            return f"Could not fetch historical data for {display}: {exc}. No cache available."

    if not result:
        return f"No USGS historical data found for {display} over the past {days_back} days."

    map_locations = []
    for s in result:
        gage = next((p for p in s["peaks"] if p["param_code"] == "00065"), None)
        flow = next((p for p in s["peaks"] if p["param_code"] == "00060"), None)
        parts = []
        if gage:
            parts.append(f"Peak height: {gage['peak_value']} {gage['unit']} on {gage['peak_date']}")
        if flow:
            parts.append(f"Peak flow: {flow['peak_value']} {flow['unit']} on {flow['peak_date']}")
        map_locations.append({
            "lat": s["lat"], "lon": s["lon"],
            "title": s["name"],
            "description": " | ".join(parts),
            "marker_type": "historical",
            "peak_value": gage["peak_value"] if gage else (flow["peak_value"] if flow else None),
            "peak_unit": gage["unit"] if gage else (flow["unit"] if flow else ""),
        })

    title = f"{display} — Flood History Last {days_back} Days"
    _write_map(map_locations, title, center=(geo["lat"], geo["lon"]))
    top = result[0]["name"] if result else "unknown"
    return (
        f"Map generated for {display} ({data_source}): {len(map_locations)} stations "
        f"over {start_dt} to {end_dt}. Highest peak at: {top}."
    )


@tool
def fetch_nws_flood_alerts() -> str:
    """Fetch active NWS flood alerts for Dallas area (raw data, no map).
    Use show_current_flood_map instead if you need a map.
    """
    try:
        data = _http_get("https://api.weather.gov/alerts/active?area=TX")
    except Exception as exc:
        cached, fetched_at = _load_cache("nws_alerts")
        if cached:
            return f"[Cached from {fetched_at}]\n{len(cached)} alerts:\n{json.dumps(cached, indent=2)}"
        return f"NWS API error: {exc}. No cache available."

    alerts = []
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        if "flood" not in props.get("event", "").lower():
            continue
        alerts.append({
            "event": props.get("event", ""),
            "severity": props.get("severity", ""),
            "areas": props.get("areaDesc", ""),
            "expires": props.get("expires", ""),
        })

    if not alerts:
        return "No active flood alerts for the Dallas area."
    _save_cache("nws_alerts", alerts)
    return f"{len(alerts)} active flood alert(s):\n{json.dumps(alerts, indent=2)}"


@tool
def fetch_usgs_flood_gauges() -> str:
    """Fetch real-time USGS stream gauge readings for Dallas area (raw data, no map).
    Use show_current_flood_map instead if you need a map.
    """
    west, south, east, north = (-97.5, 32.5, -96.5, 33.2)
    url = (
        "https://waterservices.usgs.gov/nwis/iv/?format=json"
        f"&bBox={west},{south},{east},{north}"
        "&parameterCd=00060,00065&siteStatus=active&siteType=ST"
    )
    try:
        data = _http_get(url)
    except Exception as exc:
        cached, fetched_at = _load_cache("usgs_gauges")
        if cached:
            return f"[Cached from {fetched_at}]\n{len(cached)} stations:\n{json.dumps(cached, indent=2)}"
        return f"USGS API error: {exc}. No cache available."

    stations: dict = {}
    for ts in data.get("value", {}).get("timeSeries", []):
        si = ts.get("sourceInfo", {})
        code = (si.get("siteCode") or [{}])[0].get("value", "")
        geo = si.get("geoLocation", {}).get("geogLocation", {})
        lat, lon = geo.get("latitude"), geo.get("longitude")
        if not lat or not lon:
            continue
        param = ts.get("variable", {}).get("variableName", "")
        unit = ts.get("variable", {}).get("unit", {}).get("unitCode", "")
        vals = (ts.get("values") or [{}])[0].get("value", [])
        if not vals:
            continue
        v = vals[-1]
        if not v.get("value") or v["value"] == "-999999":
            continue
        if code not in stations:
            stations[code] = {"name": si.get("siteName", ""), "lat": lat, "lon": lon, "readings": []}
        stations[code]["readings"].append({"parameter": param, "value": v["value"], "unit": unit})

    result = list(stations.values())
    if not result:
        return "No active USGS stream gauge data found for Dallas area."
    _save_cache("usgs_gauges", result)
    return f"{len(result)} stations:\n{json.dumps(result, indent=2)}"


@tool
def fetch_usgs_historical_gauges(days_back: int = 7) -> str:
    """Fetch USGS daily peak gauge readings for Dallas area over the past N days (raw data, no map).
    Use show_historical_flood_map instead if you need a map.

    Args:
        days_back: Number of past days (1-30). Default 7.
    """
    days_back = max(1, min(30, days_back))
    end_dt = date.today()
    start_dt = end_dt - timedelta(days=days_back)
    west, south, east, north = (-97.5, 32.5, -96.5, 33.2)
    cache_key = f"usgs_historical_{days_back}d"
    url = (
        "https://waterservices.usgs.gov/nwis/dv/?format=json"
        f"&bBox={west},{south},{east},{north}"
        "&parameterCd=00060,00065"
        f"&startDT={start_dt}&endDT={end_dt}"
        "&siteType=ST&siteStatus=active&statCd=00003"
    )
    try:
        data = _http_get(url)
    except Exception as exc:
        cached, fetched_at = _load_cache(cache_key)
        if cached:
            return f"[Cached from {fetched_at}]\n{json.dumps(cached, indent=2)}"
        return f"USGS historical API error: {exc}. No cache available."

    stations: dict = {}
    for ts in data.get("value", {}).get("timeSeries", []):
        si = ts.get("sourceInfo", {})
        code = (si.get("siteCode") or [{}])[0].get("value", "")
        geo = si.get("geoLocation", {}).get("geogLocation", {})
        lat, lon = geo.get("latitude"), geo.get("longitude")
        if not lat or not lon:
            continue
        param_code = (ts.get("variable", {}).get("variableCode") or [{}])[0].get("value", "")
        unit = ts.get("variable", {}).get("unit", {}).get("unitCode", "")
        vals = [v for v in (ts.get("values") or [{}])[0].get("value", [])
                if v.get("value") and v["value"] != "-999999"]
        if not vals:
            continue
        peak = max(vals, key=lambda v: float(v["value"]))
        if code not in stations:
            stations[code] = {"name": si.get("siteName", ""), "lat": lat, "lon": lon, "peaks": []}
        stations[code]["peaks"].append({
            "param_code": param_code, "peak_value": float(peak["value"]),
            "unit": unit, "peak_date": peak["dateTime"][:10],
        })

    result = sorted(
        stations.values(),
        key=lambda s: next((p["peak_value"] for p in s["peaks"] if p["param_code"] == "00065"), 0),
        reverse=True,
    )
    if not result:
        return f"No USGS historical data found for the past {days_back} days."
    _save_cache(cache_key, result)
    return f"{len(result)} stations ({start_dt} to {end_dt}):\n{json.dumps(result, indent=2)}"
