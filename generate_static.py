#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Generate static flood map snapshots.

Run this script to regenerate the committed HTML maps in static/:
    python generate_static.py

Maps written:
    static/dallas_last_week.html  — Dallas USGS gauge peaks, last 7 days
"""
import sys
from datetime import date, timedelta

sys.path.insert(0, ".")
from flood_tools import _geocode, _http_get, _generate_map


def generate_dallas_last_week(days_back: int = 7) -> None:
    geo = _geocode("Dallas, TX")
    west, south, east, north = geo["bbox"]
    end_dt = date.today()
    start_dt = end_dt - timedelta(days=days_back)

    data = _http_get(
        "https://waterservices.usgs.gov/nwis/dv/?format=json"
        f"&bBox={west},{south},{east},{north}"
        "&parameterCd=00060,00065"
        f"&startDT={start_dt}&endDT={end_dt}"
        "&siteStatus=active&statCd=00003"
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
        unit = ts.get("variable", {}).get("unit", {}).get("unitCode", "")
        vals = [
            v for v in (ts.get("values") or [{}])[0].get("value", [])
            if v.get("value") and v["value"] != "-999999"
        ]
        if not vals:
            continue
        peak = max(vals, key=lambda v: float(v["value"]))
        if code not in stations:
            stations[code] = {"name": si.get("siteName", ""), "lat": lat, "lon": lon, "peaks": []}
        stations[code]["peaks"].append({
            "param_code": param_code,
            "peak_value": float(peak["value"]),
            "unit": unit,
            "peak_date": peak["dateTime"][:10],
        })

    result = sorted(
        stations.values(),
        key=lambda s: next(
            (p["peak_value"] for p in s["peaks"] if p["param_code"] == "00065"), 0
        ),
        reverse=True,
    )

    locations = []
    for s in result:
        gage = next((p for p in s["peaks"] if p["param_code"] == "00065"), None)
        flow = next((p for p in s["peaks"] if p["param_code"] == "00060"), None)
        parts = []
        if gage:
            parts.append(f"Peak height: {gage['peak_value']} {gage['unit']} on {gage['peak_date']}")
        if flow:
            parts.append(f"Peak flow: {flow['peak_value']} {flow['unit']} on {flow['peak_date']}")
        locations.append({
            "lat": s["lat"],
            "lon": s["lon"],
            "title": s["name"],
            "description": " | ".join(parts),
            "marker_type": "historical",
            "peak_value": gage["peak_value"] if gage else (flow["peak_value"] if flow else None),
            "peak_unit": gage["unit"] if gage else (flow["unit"] if flow else ""),
        })

    title = f"Dallas — Flood History {start_dt} to {end_dt}"
    msg = _generate_map(
        locations,
        title=title,
        output_filename="static/dallas_last_week.html",
        center=(geo["lat"], geo["lon"]),
    )
    print(msg)
    print(f"{len(locations)} stations plotted.")


if __name__ == "__main__":
    generate_dallas_last_week()
