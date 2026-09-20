"""Weather tool: location string → current conditions via Open-Meteo (no API key)."""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Optional

# Geocoding and forecast APIs (no key required)
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather codes (subset for readable summary)
WEATHER_DESCRIPTIONS = {
    0: "clear",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "foggy",
    48: "depositing rime fog",
    51: "light drizzle",
    53: "drizzle",
    55: "dense drizzle",
    61: "slight rain",
    63: "rain",
    65: "heavy rain",
    71: "slight snow",
    73: "snow",
    75: "heavy snow",
    80: "slight rain showers",
    81: "rain showers",
    82: "heavy rain showers",
    95: "thunderstorm",
    96: "thunderstorm with hail",
    99: "thunderstorm with heavy hail",
}

def _geocode(location: str) -> Optional[tuple[float, float, str]]:
    """Resolve location string to (lat, lon, display_name). Returns None if not found."""
    location = (location or "").strip()
    if not location:
        return None
    params = urllib.parse.urlencode({"name": location, "count": 1})
    url = f"{GEOCODE_URL}?{params}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    results = data.get("results") or []
    if not results:
        return None
    r = results[0]
    lat = r.get("latitude")
    lon = r.get("longitude")
    name = r.get("name") or location
    if lat is None or lon is None:
        return None
    return (float(lat), float(lon), str(name))


def _fetch_weather_json(lat: float, lon: float) -> Optional[dict]:
    """Get current weather for lat/lon from Open-Meteo JSON API (no library required)."""
    params = urllib.parse.urlencode({
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
    })
    url = f"{FORECAST_URL}?{params}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def get_weather(location: str) -> str:
    """
    Get current weather for a location (city name or place).
    Uses Open-Meteo JSON API; no API key required.
    Returns a short human-readable string or an error message.
    """
    loc = _geocode(location)
    if not loc:
        return f"Could not find location: {location!r}. Try a city name or place."
    lat, lon, name = loc

    data = _fetch_weather_json(lat, lon)
    if not data:
        return f"Weather unavailable for {name}."

    current = data.get("current") or {}
    temp = current.get("temperature_2m")
    unit = (data.get("current_units") or {}).get("temperature_2m", "°C")
    humidity = current.get("relative_humidity_2m")
    code = current.get("weather_code", 0)
    wind = current.get("wind_speed_10m")
    wind_unit = (data.get("current_units") or {}).get("wind_speed_10m", "km/h")

    desc = WEATHER_DESCRIPTIONS.get(int(code) if code is not None else 0, f"code {code}")
    parts = [f"{name}: {desc}"]
    if temp is not None:
        parts.append(f" {temp}{unit}")
    if humidity is not None:
        parts.append(f", humidity {humidity}%")
    if wind is not None:
        parts.append(f", wind {wind} {wind_unit}")
    return "".join(parts).strip()


def _is_weather_or_temperature_query(message: str) -> bool:
    """True if the message is asking about weather, temperature, or forecast."""
    lower = (message or "").strip().lower()
    triggers = (
        "weather", "temperature", "temp ", "forecast", "how hot", "how cold",
        "degrees in", "degrees for", "current temp", "current temperature",
        "what's the temp", "whats the temp", "what's the temperature",
    )
    return any(t in lower for t in triggers)


def _extract_weather_location(message: str) -> Optional[str]:
    """Extract a place name from a weather/temperature-related message. Returns None if not relevant."""
    lower = (message or "").strip().lower()
    if not _is_weather_or_temperature_query(lower):
        return None
    # "temperature in X", "current temperature in South San Francisco", "weather in X", "forecast for X"
    for pattern in (
        r"(?:temperature|temp|weather|forecast)\s+(?:in|for|at)\s+([^?.!]+?)(?:\?|\.|!|$)",
        r"(?:in|for|at)\s+([^?.!]+?)\s*(?:\?|\.|!|$)(?=.*(?:weather|temperature|temp|forecast))",
        r"the\s+(?:weather|temperature|temp)\s+(?:in|for|at)\s+([^?.!]+?)(?:\?|\.|!|$)",
        r"current\s+(?:temperature|temp)[^?.!]*\s+(?:in|for|at)\s+([^?.!]+?)(?:\?|\.|!|$)",
        r"(?:in|for|at)\s+([^?.!]+?)\s*(?:\?|\.|!|$)",  # "in South San Francisco" with weather/temp elsewhere
    ):
        m = re.search(pattern, lower, re.IGNORECASE | re.DOTALL)
        if m:
            loc = m.group(1).strip().strip(".,;:")
            if loc and len(loc) <= 80 and loc not in ("it", "there", "here"):
                return loc
    # "what's the weather/temp" with optional "in X" before or after
    rest = re.sub(
        r"(?:what'?s?\s+the\s+)?(?:weather|temperature|temp)(?:\s+in|\s+for|\s+at)?\s*",
        "", lower, flags=re.IGNORECASE
    )
    rest = re.sub(r"^(?:in|for|at)\s*", "", rest.strip()).strip(".,;? ")
    if rest and len(rest) <= 80:
        return rest
    return "current location"


def _location_from_recent_turns(recent_turns: Optional[list[dict]]) -> Optional[str]:
    """
    Try to get a location from the conversation (e.g. last weather tool or last message mentioning a place).
    So follow-ups like "what's the exact temperature?" use the same place (e.g. San Francisco).
    """
    if not recent_turns:
        return None
    # Look at the last few messages (newest first)
    for m in reversed(recent_turns[-10:]):
        content = (m.get("content") or "").strip()
        role = (m.get("role") or "").strip().lower()
        # Last weather tool: we stored {"name":"weather","input":"San Francisco","result":"..."}
        if role == "tool" and content:
            try:
                data = json.loads(content)
                if data.get("name") == "weather" and data.get("input"):
                    return data.get("input")
            except (json.JSONDecodeError, TypeError):
                pass
        # User or assistant mentioned "in X" / "for X" (simple place extraction)
        if role in ("user", "assistant") and content:
            match = re.search(
                r"(?:weather|temperature|temp|forecast)\s+(?:in|for|at)\s+([^?.!,]+?)(?:\?|\.|!|,|$)",
                content, re.IGNORECASE
            )
            if match:
                loc = match.group(1).strip().strip(".,;:")
                if loc and len(loc) <= 80:
                    return loc
    return None


def try_weather_tool(
    message: str,
    recent_turns: Optional[list[dict]] = None,
) -> Optional[tuple[str, str]]:
    """
    If message looks like a weather query, return (location, tool_result). Else None.
    When the message doesn't name a place (e.g. "what's the exact temperature?"), use
    recent_turns to get the last location from this conversation.
    """
    location = _extract_weather_location(message or "")
    if not location:
        return None
    if location == "current location":
        location = _location_from_recent_turns(recent_turns)
        if not location:
            return None
    result = get_weather(location)
    return (location, result)
