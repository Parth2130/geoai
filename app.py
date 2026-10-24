import asyncio
import html
import os
from datetime import date, timedelta
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


# =========================================================
# GEOAI OUTDOOR ACTIVITY ASSISTANT
# Complete frontend and backend in one Python file
# =========================================================

app = FastAPI(
    title="GeoAI Outdoor Activity Assistant",
    description="Weather-aware outdoor activity recommendations.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

HTTP_TIMEOUT = 15.0

ACTIVITIES = {
    "running": {
        "name": "Running",
        "icon": "🏃",
        "description": "Stay active with a refreshing outdoor run.",
        "ideal_temp": (10, 28),
        "max_wind": 30,
        "max_rain": 30,
    },
    "cycling": {
        "name": "Cycling",
        "icon": "🚴",
        "description": "Explore scenic roads and cycling routes.",
        "ideal_temp": (12, 30),
        "max_wind": 22,
        "max_rain": 20,
    },
    "hiking": {
        "name": "Hiking",
        "icon": "🥾",
        "description": "Discover nature and enjoy the outdoors.",
        "ideal_temp": (10, 27),
        "max_wind": 25,
        "max_rain": 20,
    },
    "cricket": {
        "name": "Cricket",
        "icon": "🏏",
        "description": "Find a suitable time for a cricket match.",
        "ideal_temp": (15, 33),
        "max_wind": 25,
        "max_rain": 20,
    },
    "football": {
        "name": "Football",
        "icon": "⚽",
        "description": "Plan your next outdoor football session.",
        "ideal_temp": (12, 30),
        "max_wind": 25,
        "max_rain": 25,
    },
    "picnic": {
        "name": "Picnic",
        "icon": "🧺",
        "description": "Enjoy a relaxing day outdoors.",
        "ideal_temp": (18, 30),
        "max_wind": 20,
        "max_rain": 10,
    },
    "photography": {
        "name": "Photography",
        "icon": "📸",
        "description": "Discover beautiful outdoor photo opportunities.",
        "ideal_temp": (5, 35),
        "max_wind": 30,
        "max_rain": 35,
    },
    "tennis": {
        "name": "Tennis",
        "icon": "🎾",
        "description": "Plan an enjoyable outdoor tennis session.",
        "ideal_temp": (12, 29),
        "max_wind": 20,
        "max_rain": 15,
    },
}


class RecommendationRequest(BaseModel):
    location: str = Field(min_length=2, max_length=120)
    activity: str = "running"
    activity_date: Optional[date] = None
    preferred_time: str = "any"


class AssistantRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    location: str = "Pune, India"


async def get_json(client, url, params=None):
    response = await client.get(url, params=params)
    response.raise_for_status()
    return response.json()


async def get_location(client, location_name):
    data = await get_json(
        client,
        "https://geocoding-api.open-meteo.com/v1/search",
        {
            "name": location_name,
            "count": 1,
            "language": "en",
            "format": "json",
        },
    )

    results = data.get("results", [])

    if not results:
        raise HTTPException(
            status_code=404,
            detail=f"Location '{location_name}' was not found. Try a city name such as Pune.",
        )

    place = results[0]

    return {
        "name": place.get("name", location_name),
        "country": place.get("country", ""),
        "latitude": place["latitude"],
        "longitude": place["longitude"],
        "timezone": place.get("timezone", "auto"),
    }


async def get_weather(client, location):
    data = await get_json(
        client,
        "https://api.open-meteo.com/v1/forecast",
        {
            "latitude": location["latitude"],
            "longitude": location["longitude"],
            "timezone": location["timezone"],
            "forecast_days": 7,
            "current": (
                "temperature_2m,relative_humidity_2m,"
                "apparent_temperature,is_day,precipitation,"
                "weather_code,wind_speed_10m"
            ),
            "hourly": (
                "temperature_2m,precipitation_probability,"
                "precipitation,wind_speed_10m,"
                "relative_humidity_2m,weather_code"
            ),
            "daily": (
                "weather_code,temperature_2m_max,"
                "temperature_2m_min,precipitation_probability_max,"
                "sunrise,sunset"
            ),
        },
    )

    return data


async def get_air_quality(client, location):
    try:
        data = await get_json(
            client,
            "https://air-quality-api.open-meteo.com/v1/air-quality",
            {
                "latitude": location["latitude"],
                "longitude": location["longitude"],
                "timezone": location["timezone"],
                "current": (
                    "european_aqi,us_aqi,pm10,pm2_5,"
                    "carbon_monoxide,nitrogen_dioxide"
                ),
            },
        )

        return {
            "available": True,
            "current": data.get("current", {}),
        }

    except Exception:
        return {
            "available": False,
            "current": {},
        }


async def get_nearby_places(client, location):
    query = f"""
    [out:json][timeout:10];
    (
      node["leisure"~"park|sports_centre|pitch"]
        (around:5000,{location["latitude"]},{location["longitude"]});
      way["leisure"~"park|sports_centre|pitch"]
        (around:5000,{location["latitude"]},{location["longitude"]});
      node["tourism"="viewpoint"]
        (around:5000,{location["latitude"]},{location["longitude"]});
    );
    out center 15;
    """

    try:
        data = await get_json(
            client,
            "https://overpass-api.de/api/interpreter",
            {"data": query},
        )

        places = []

        for item in data.get("elements", [])[:12]:
            tags = item.get("tags", {})
            name = tags.get("name")

            if not name:
                continue

            lat = item.get("lat")
            lon = item.get("lon")

            if item.get("type") == "way":
                center = item.get("center", {})
                lat = center.get("lat")
                lon = center.get("lon")

            places.append({
                "name": name,
                "type": tags.get("leisure")
                        or tags.get("tourism")
                        or "outdoor place",
                "latitude": lat,
                "longitude": lon,
            })

        return places

    except Exception:
        return []


def calculate_activity_score(activity, temperature, rain, wind, aqi):
    details = ACTIVITIES.get(activity, ACTIVITIES["running"])

    low, high = details["ideal_temp"]
    score = 100
    reasons = []

    if temperature < low:
        score -= min(30, (low - temperature) * 3)
        reasons.append("The temperature is cooler than ideal.")

    elif temperature > high:
        score -= min(35, (temperature - high) * 4)
        reasons.append("The temperature is warmer than ideal.")

    if rain > details["max_rain"]:
        score -= min(40, (rain - details["max_rain"]) * 1.5)
        reasons.append("Rain may affect the activity.")

    if wind > details["max_wind"]:
        score -= min(25, (wind - details["max_wind"]) * 1.5)
        reasons.append("Strong winds may make the activity difficult.")

    if aqi is not None:
        if aqi > 100:
            score -= 35
            reasons.append("Air quality may be unhealthy for outdoor exercise.")
        elif aqi > 50:
            score -= 15
            reasons.append("Air quality is moderate; sensitive people should take care.")

    score = max(0, min(100, round(score)))

    if score >= 80:
        status = "Excellent"
    elif score >= 60:
        status = "Good"
    elif score >= 40:
        status = "Fair"
    else:
        status = "Poor"

    if not reasons:
        reasons.append("Weather conditions appear suitable based on the available data.")

    return {
        "score": score,
        "status": status,
        "reasons": reasons,
    }


def build_recommendations(weather, air_quality, requested_date, preferred_time):
    hourly = weather.get("hourly", {})
    times = hourly.get("time", [])

    if requested_date is None:
        requested_date = date.today()

    target_date = requested_date.isoformat()

    aqi = air_quality.get("current", {}).get("us_aqi")

    results = []

    for i, timestamp in enumerate(times):
        if not timestamp.startswith(target_date):
            continue

        hour = int(timestamp[11:13])

        if preferred_time == "morning" and not 5 <= hour <= 11:
            continue

        if preferred_time == "afternoon" and not 12 <= hour <= 16:
            continue

        if preferred_time == "evening" and not 17 <= hour <= 20:
            continue

        temp = (hourly.get("temperature_2m") or [None])[i]
        rain = (hourly.get("precipitation_probability") or [None])[i]
        wind = (hourly.get("wind_speed_10m") or [None])[i]

        if temp is None:
            continue

        results.append({
            "time": timestamp[11:16],
            "temperature": temp,
            "rain_probability": rain or 0,
            "wind_speed": wind or 0,
            "aqi": aqi,
        })

    return results


@app.get("/", response_class=HTMLResponse)
async def home():
    return HTMLResponse(HTML_PAGE)


@app.get("/api/health")
async def health():
    return {
        "status": "online",
        "application": "GeoAI Outdoor Activity Assistant",
        "activities": list(ACTIVITIES.keys()),
    }


@app.get("/api/activities")
async def activities():
    return ACTIVITIES


@app.post("/api/recommend")
async def recommend(request: RecommendationRequest):
    activity = request.activity.lower().strip()

    if activity not in ACTIVITIES:
        raise HTTPException(
            status_code=400,
            detail="Choose a valid activity from the available list.",
        )

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        location, weather, air = await asyncio.gather(
            get_location(client, request.location),
            get_weather_for_request(client, request.location),
            get_air_for_request(client, request.location),
            return_exceptions=True,
        )

    if isinstance(location, Exception):
        if isinstance(location, HTTPException):
            raise location
        raise HTTPException(
            status_code=502,
            detail="Could not find the location. Check your internet connection and try again.",
        )

    if isinstance(weather, Exception):
        raise HTTPException(
            status_code=502,
            detail="Weather data is temporarily unavailable. Please try again.",
        )

    if isinstance(air, Exception):
        air = {"available": False, "current": {}}

    current = weather.get("current", {})
    aqi = air.get("current", {}).get("us_aqi")

    temp = current.get("temperature_2m", 25)
    wind = current.get("wind_speed_10m", 0)
    rain = current.get("precipitation", 0)

    result = calculate_activity_score(
        activity,
        temp,
        rain,
        wind,
        aqi,
    )

    hourly_slots = build_recommendations(
        weather,
        air,
        request.activity_date,
        request.preferred_time,
    )

    best_times = []

    for slot in hourly_slots:
        score_data = calculate_activity_score(
            activity,
            slot["temperature"],
            slot["rain_probability"],
            slot["wind_speed"],
            aqi,
        )

        best_times.append({
            **slot,
            **score_data,
        })

    best_times.sort(key=lambda x: x["score"], reverse=True)

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        places = await get_nearby_places(client, location)

    return {
        "location": location,
        "activity": ACTIVITIES[activity],
        "current_weather": current,
        "air_quality": air,
        "current_recommendation": result,
        "best_times": best_times[:5],
        "nearby_places": places,
        "forecast": weather.get("daily", {}),
        "disclaimer": (
            "Recommendations are estimates based on forecast data, "
            "not a guarantee of safety. Check official weather alerts "
            "and local conditions before outdoor activities."
        ),
    }


async def get_weather_for_request(client, location_name):
    location = await get_location(client, location_name)
    return await get_weather(client, location)


async def get_air_for_request(client, location_name):
    location = await get_location(client, location_name)
    return await get_air_quality(client, location)


@app.post("/api/assistant")
async def assistant(request: AssistantRequest):
    message = request.message.lower()

    if any(word in message for word in ["weather", "temperature", "rain"]):
        answer = (
            f"To check the latest weather for {request.location}, "
            "use the Activity Planner. It retrieves forecast information "
            "and estimates suitability for your chosen activity."
        )

    elif any(word in message for word in ["air", "pollution", "aqi"]):
        answer = (
            "Air quality matters during outdoor exercise. Check the AQI "
            "before going outside. If pollution is high, consider "
            "an indoor activity or postpone strenuous exercise."
        )

    elif any(word in message for word in ["running", "run", "jog"]):
        answer = (
            "Running is generally more comfortable in moderate temperatures, "
            "with low wind, little rain and good air quality. Use the planner "
            "to compare forecasted hours for your location."
        )

    elif any(word in message for word in ["cycling", "bike"]):
        answer = (
            "For cycling, look for low wind, little rain and comfortable "
            "temperatures. Check your route and wear a helmet."
        )

    elif any(word in message for word in ["hiking", "trek"]):
        answer = (
            "Before hiking, check the forecast, trail conditions, daylight "
            "and local warnings. Carry water and tell someone your route."
        )

    elif any(word in message for word in ["cricket", "football", "tennis"]):
        answer = (
            "Choose a time with comfortable temperatures, low rain probability "
            "and manageable wind. The Activity Planner can compare forecast "
            "hours for your selected sport."
        )

    elif any(word in message for word in ["hello", "hi", "hey"]):
        answer = (
            "Hello! I'm GeoAI, your outdoor activity assistant. "
            "Ask me about weather, air quality, running, cycling, hiking "
            "or planning an outdoor activity."
        )

    else:
        answer = (
            "I can help you plan outdoor activities using weather and "
            "air-quality information. Try asking about running, cycling, "
            "hiking, sports, temperature, rain or pollution."
        )

    return {
        "reply": answer,
        "location": request.location,
        "assistant_type": "Rule-based demonstration assistant",
    }


# =========================================================
# FRONTEND: HTML + CSS + JAVASCRIPT
# =========================================================

HTML_PAGE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GeoAI | Outdoor Activity Assistant</title>

<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Manrope:wght@400;500;600;700;800&display=swap" rel="stylesheet">

<link rel="stylesheet"
 href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">

<style>
:root {
    --green: #16866a;
    --dark-green: #075943;
    --mint: #dff8ed;
    --pale: #f3fbf7;
    --aqua: #dff7f5;
    --orange: #ffb36a;
    --text: #173b32;
    --muted: #698178;
    --white: #ffffff;
    --border: #e1eee7;
    --shadow: 0 16px 45px rgba(17, 83, 63, 0.09);
}

* {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
    scroll-behavior: smooth;
}

body {
    font-family: 'DM Sans', sans-serif;
    color: var(--text);
    background: #f9fcfa;
    line-height: 1.6;
}

h1, h2, h3, h4 {
    font-family: 'Manrope', sans-serif;
    line-height: 1.2;
}

button, input, select {
    font: inherit;
}

button {
    cursor: pointer;
}

a {
    color: inherit;
    text-decoration: none;
}

.container {
    width: min(1160px, 92%);
    margin: auto;
}

nav {
    background: rgba(255,255,255,.94);
    backdrop-filter: blur(12px);
    position: sticky;
    top: 0;
    z-index: 1000;
    border-bottom: 1px solid var(--border);
}

.nav-inner {
    min-height: 76px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 24px;
}

.logo {
    font-family: 'Manrope', sans-serif;
    font-size: 23px;
    font-weight: 800;
    color: var(--dark-green);
}

.logo span {
    color: #35b98a;
}

.nav-links {
    display: flex;
    align-items: center;
    gap: 25px;
    font-size: 14px;
    font-weight: 600;
}

.nav-links a:hover {
    color: var(--green);
}

.btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 13px 21px;
    border: 0;
    border-radius: 12px;
    background: var(--green);
    color: white;
    font-weight: 700;
    transition: .2s;
}

.btn:hover {
    background: var(--dark-green);
    transform: translateY(-2px);
}

.btn-light {
    color: var(--dark-green);
    background: white;
    border: 1px solid var(--border);
}

.hero {
    padding: 75px 0 65px;
    overflow: hidden;
    background:
      radial-gradient(circle at 80% 20%, #d7f6e6 0, transparent 32%),
      linear-gradient(135deg, #f5fcf7, #e8f9f4);
}

.hero-grid {
    display: grid;
    grid-template-columns: 1.05fr .95fr;
    align-items: center;
    gap: 50px;
}

.eyebrow {
    display: inline-block;
    padding: 8px 13px;
    border-radius: 30px;
    background: #d9f5e7;
    color: var(--dark-green);
    font-weight: 700;
    font-size: 12px;
    letter-spacing: .4px;
    margin-bottom: 20px;
}

.hero h1 {
    font-size: clamp(38px, 5vw, 65px);
    letter-spacing: -2px;
    margin-bottom: 22px;
}

.hero h1 span {
    color: var(--green);
}

.hero p {
    font-size: 17px;
    color: var(--muted);
    max-width: 540px;
    margin-bottom: 28px;
}

.hero-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
}

.hero-image {
    position: relative;
}

.hero-image img {
    display: block;
    width: 100%;
    height: 430px;
    object-fit: cover;
    border-radius: 28px;
    box-shadow: var(--shadow);
}

.floating-card {
    position: absolute;
    bottom: 24px;
    left: -25px;
    padding: 18px;
    border-radius: 17px;
    background: white;
    box-shadow: var(--shadow);
    min-width: 205px;
}

.floating-card strong {
    display: block;
    font-size: 25px;
    color: var(--green);
}

.floating-card small {
    color: var(--muted);
}

.section {
    padding: 80px 0;
}

.section-heading {
    text-align: center;
    max-width: 650px;
    margin: 0 auto 38px;
}

.section-heading h2 {
    font-size: clamp(29px, 4vw, 42px);
    margin-bottom: 13px;
}

.section-heading p {
    color: var(--muted);
}

.activity-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 19px;
}

.activity-card {
    padding: 24px;
    background: white;
    border: 1px solid var(--border);
    border-radius: 19px;
    transition: .25s;
}

.activity-card:hover {
    transform: translateY(-5px);
    box-shadow: var(--shadow);
    border-color: #9edcc3;
}

.activity-icon {
    width: 54px;
    height: 54px;
    display: grid;
    place-items: center;
    border-radius: 15px;
    background: var(--mint);
    font-size: 27px;
    margin-bottom: 18px;
}

.activity-card h3 {
    margin-bottom: 9px;
    font-size: 18px;
}

.activity-card p {
    font-size: 13px;
    color: var(--muted);
}

.planner-section {
    background: linear-gradient(135deg, #e8f8f0, #effbfa);
}

.planner-layout {
    display: grid;
    grid-template-columns: .9fr 1.1fr;
    gap: 28px;
    align-items: start;
}

.panel {
    padding: 30px;
    background: white;
    border: 1px solid var(--border);
    border-radius: 22px;
    box-shadow: 0 10px 30px rgba(20,80,60,.04);
}

.panel h3 {
    margin-bottom: 8px;
    font-size: 23px;
}

.panel-subtitle {
    color: var(--muted);
    margin-bottom: 23px;
    font-size: 14px;
}

.form-group {
    margin-bottom: 18px;
}

.form-group label {
    display: block;
    font-size: 13px;
    font-weight: 700;
    margin-bottom: 8px;
}

.form-group input,
.form-group select {
    width: 100%;
    padding: 13px 14px;
    border: 1px solid #dbe9e1;
    border-radius: 11px;
    background: #fbfefc;
    color: var(--text);
    outline: none;
}

.form-group input:focus,
.form-group select:focus {
    border-color: var(--green);
    box-shadow: 0 0 0 3px #16866a18;
}

.full {
    width: 100%;
}

.status {
    margin-top: 16px;
    font-size: 13px;
    color: var(--muted);
    overflow-wrap: anywhere;
}

.results {
    display: grid;
    gap: 17px;
}

.empty-state {
    text-align: center;
    padding: 55px 20px;
    background: #f8fcf9;
    border: 1px dashed #cce5d8;
    border-radius: 17px;
}

.empty-state .big-icon {
    font-size: 46px;
    margin-bottom: 14px;
}

.empty-state p {
    color: var(--muted);
    margin-top: 9px;
    font-size: 14px;
}

.score-card {
    background: linear-gradient(120deg, #0c7759, #20a77b);
    border-radius: 20px;
    padding: 25px;
    color: white;
}

.score-card h3 {
    margin: 8px 0;
    font-size: 27px;
}

.score-number {
    font-size: 43px;
    font-weight: 800;
    font-family: 'Manrope', sans-serif;
}

.score-track {
    height: 8px;
    background: #ffffff55;
    border-radius: 9px;
    overflow: hidden;
    margin: 13px 0;
}

.score-fill {
    height: 100%;
    background: #ffcc86;
    border-radius: 9px;
}

.data-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 12px;
}

.data-tile {
    background: #f3faf6;
    border-radius: 13px;
    padding: 16px;
}

.data-tile small {
    display: block;
    color: var(--muted);
    margin-bottom: 6px;
}

.data-tile strong {
    font-size: 22px;
}

.result-card {
    padding: 22px;
    background: white;
    border: 1px solid var(--border);
    border-radius: 17px;
}

.result-card h4 {
    margin-bottom: 12px;
}

.result-list {
    list-style: none;
    display: grid;
    gap: 8px;
    font-size: 14px;
    color: var(--muted);
}

.result-list li::before {
    content: "✓";
    color: var(--green);
    font-weight: 800;
    margin-right: 9px;
}

.time-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 15px;
    padding: 13px 0;
    border-bottom: 1px solid var(--border);
}

.time-row:last-child {
    border-bottom: 0;
}

.time-row strong {
    display: block;
}

.time-row small {
    color: var(--muted);
}

.badge {
    padding: 6px 10px;
    border-radius: 30px;
    font-size: 12px;
    font-weight: 800;
    background: var(--mint);
    color: var(--dark-green);
    white-space: nowrap;
}

.dashboard-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 20px;
}

.info-card {
    background: white;
    border: 1px solid var(--border);
    border-radius: 19px;
    padding: 25px;
}

.info-card .icon {
    font-size: 30px;
    margin-bottom: 14px;
}

.info-card h3 {
    margin-bottom: 9px;
}

.info-card p {
    color: var(--muted);
    font-size: 14px;
}

.map-section {
    background: #f0faf5;
}

#map {
    height: 390px;
    width: 100%;
    border-radius: 20px;
    border: 1px solid var(--border);
    z-index: 1;
}

.places-list {
    margin-top: 20px;
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 12px;
}

.place-item {
    padding: 16px;
    border-radius: 13px;
    background: white;
    border: 1px solid var(--border);
    font-size: 14px;
}

.place-item small {
    display: block;
    color: var(--muted);
    margin-top: 5px;
}

.safety-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 35px;
    align-items: center;
}

.safety-image img {
    width: 100%;
    height: 380px;
    object-fit: cover;
    border-radius: 24px;
}

.safety-list {
    list-style: none;
    margin-top: 23px;
    display: grid;
    gap: 15px;
}

.safety-list li {
    padding: 15px;
    background: white;
    border: 1px solid var(--border);
    border-radius: 13px;
}

.safety-list strong {
    display: block;
    margin-bottom: 3px;
}

.safety-list span {
    color: var(--muted);
    font-size: 14px;
}

.chat-box {
    max-width: 760px;
    margin: auto;
    background: white;
    border: 1px solid var(--border);
    border-radius: 22px;
    box-shadow: var(--shadow);
    overflow: hidden;
}

.chat-header {
    padding: 23px;
    background: linear-gradient(120deg, #0b7556, #1caa7c);
    color: white;
}

.chat-header p {
    font-size: 13px;
    opacity: .85;
    margin-top: 5px;
}

.chat-messages {
    height: 310px;
    overflow-y: auto;
    padding: 22px;
    display: flex;
    flex-direction: column;
    gap: 13px;
}

.message {
    max-width: 85%;
    padding: 12px 15px;
    border-radius: 15px;
    font-size: 14px;
}

.message.bot {
    background: #edf8f2;
    align-self: flex-start;
}

.message.user {
    background: var(--green);
    color: white;
    align-self: flex-end;
}

.chat-form {
    display: flex;
    gap: 10px;
    padding: 17px;
    border-top: 1px solid var(--border);
}

.chat-form input {
    flex: 1;
    min-width: 0;
    padding: 13px;
    border: 1px solid var(--border);
    border-radius: 11px;
}

.about-box {
    background: linear-gradient(125deg, #0a6d50, #20a980);
    color: white;
    padding: 50px;
    border-radius: 25px;
    text-align: center;
}

.about-box h2 {
    font-size: 35px;
    margin-bottom: 15px;
}

.about-box p {
    max-width: 750px;
    margin: auto;
    opacity: .9;
}

footer {
    padding: 35px 0;
    background: #103d31;
    color: white;
}

.footer-inner {
    display: flex;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 20px;
}

footer p {
    font-size: 13px;
    color: #c0ded2;
}

footer a {
    color: #d7f7e7;
}

.disclaimer {
    font-size: 12px;
    color: var(--muted);
    line-height: 1.7;
}

@media (max-width: 900px) {
    .hero-grid,
    .planner-layout,
    .safety-grid {
        grid-template-columns: 1fr;
    }

    .activity-grid {
        grid-template-columns: repeat(2, 1fr);
    }

    .dashboard-grid {
        grid-template-columns: repeat(2, 1fr);
    }

    .hero-image img {
        height: 340px;
    }

    .floating-card {
        left: 12px;
    }

    .nav-links {
        gap: 12px;
    }

    .nav-links a:not(.btn) {
        display: none;
    }
}

@media (max-width: 550px) {
    .section {
        padding: 55px 0;
    }

    .activity-grid,
    .dashboard-grid,
    .data-grid,
    .places-list {
        grid-template-columns: 1fr;
    }

    .panel {
        padding: 20px;
    }

    .hero {
        padding-top: 45px;
    }

    .hero h1 {
        letter-spacing: -1px;
    }

    .about-box {
        padding: 30px 20px;
    }

    .chat-form .btn {
        padding: 12px;
    }
}
</style>
</head>

<body>

<nav>
    <div class="container nav-inner">
        <a href="#home" class="logo">Geo<span>AI</span> 🌿</a>

        <div class="nav-links">
            <a href="#activities">Activities</a>
            <a href="#planner">Planner</a>
            <a href="#map-section">Explore</a>
            <a href="#safety">Safety</a>
            <a href="#assistant">AI Assistant</a>
            <a href="#planner" class="btn">Get Started →</a>
        </div>
    </div>
</nav>

<header class="hero" id="home">
    <div class="container hero-grid">
        <div>
            <span class="eyebrow">🌍 SMARTER OUTDOOR LIVING</span>

            <h1>Go outside.<br>Stay <span>weather-wise.</span></h1>

            <p>
                Your intelligent outdoor activity companion.
                Discover the best time for running, cycling, hiking
                and more using location, weather and air-quality data.
            </p>

            <div class="hero-actions">
                <a href="#planner" class="btn">Plan my activity ↗</a>
                <a href="#activities" class="btn btn-light">Explore activities</a>
            </div>
        </div>

        <div class="hero-image">
            <img
                src="https://images.unsplash.com/photo-1470770841072-f978cf4d019e?auto=format&fit=crop&w=1100&q=85"
                alt="Beautiful green mountains and lake"
                onerror="this.style.display='none'">

            <div class="floating-card">
                <span>🌱 Your outdoor companion</span>
                <strong>Powered by data</strong>
                <small>Weather • Air • Location</small>
            </div>
        </div>
    </div>
</header>

<section class="section" id="activities">
    <div class="container">
        <div class="section-heading">
            <span class="eyebrow">FIND YOUR OUTDOOR MOMENT</span>
            <h2>Made for your kind of adventure</h2>
            <p>
                Select your favourite activity and discover
                weather-aware recommendations for your day.
            </p>
        </div>

        <div class="activity-grid">
            <article class="activity-card">
                <div class="activity-icon">🏃</div>
                <h3>Running</h3>
                <p>Find comfortable hours for your daily run.</p>
            </article>

            <article class="activity-card">
                <div class="activity-icon">🚴</div>
                <h3>Cycling</h3>
                <p>Plan rides with wind and rainfall in mind.</p>
            </article>

            <article class="activity-card">
                <div class="activity-icon">🥾</div>
                <h3>Hiking</h3>
                <p>Explore nature with smarter planning.</p>
            </article>

            <article class="activity-card">
                <div class="activity-icon">🏏</div>
                <h3>Cricket</h3>
                <p>Pick a promising time for your next match.</p>
            </article>

            <article class="activity-card">
                <div class="activity-icon">⚽</div>
                <h3>Football</h3>
                <p>Make outdoor sports easier to schedule.</p>
            </article>

            <article class="activity-card">
                <div class="activity-icon">🧺</div>
                <h3>Picnic</h3>
                <p>Find a more comfortable time outdoors.</p>
            </article>

            <article class="activity-card">
                <div class="activity-icon">📸</div>
                <h3>Photography</h3>
                <p>Discover the conditions for outdoor shoots.</p>
            </article>

            <article class="activity-card">
                <div class="activity-icon">🎾</div>
                <h3>Tennis</h3>
                <p>Plan your outdoor court session.</p>
            </article>
        </div>
    </div>
</section>

<section class="section planner-section" id="planner">
    <div class="container">
        <div class="section-heading">
            <span class="eyebrow">YOUR PERSONAL ACTIVITY PLANNER</span>
            <h2>Find your best outdoor time</h2>
            <p>
                Enter a location and activity to receive
                forecast-based recommendations.
            </p>
        </div>

        <div class="planner-layout">
            <div class="panel">
                <h3>Plan your day ✨</h3>
                <p class="panel-subtitle">
                    Tell us what you would like to do.
                </p>

                <form id="plannerForm">
                    <div class="form-group">
                        <label for="location">📍 Your location</label>
                        <input
                            id="location"
                            type="text"
                            value="Pune, India"
                            placeholder="e.g. Pune, India"
                            required>
                    </div>

                    <div class="form-group">
                        <label for="activity">🏅 Select activity</label>
                        <select id="activity">
                            <option value="running">🏃 Running</option>
                            <option value="cycling">🚴 Cycling</option>
                            <option value="hiking">🥾 Hiking</option>
                            <option value="cricket">🏏 Cricket</option>
                            <option value="football">⚽ Football</option>
                            <option value="picnic">🧺 Picnic</option>
                            <option value="photography">📸 Photography</option>
                            <option value="tennis">🎾 Tennis</option>
                        </select>
                    </div>

                    <div class="form-group">
                        <label for="activityDate">📅 Activity date</label>
                        <input
                            id="activityDate"
                            type="date"
                            required>
                    </div>

                    <div class="form-group">
                        <label for="preferredTime">🕒 Preferred time</label>
                        <select id="preferredTime">
                            <option value="any">Any time</option>
                            <option value="morning">Morning (5 AM–11 AM)</option>
                            <option value="afternoon">Afternoon (12 PM–4 PM)</option>
                            <option value="evening">Evening (5 PM–8 PM)</option>
                        </select>
                    </div>

                    <button type="submit" class="btn full" id="planButton">
                        ✨ Find best time
                    </button>

                    <div class="status" id="plannerStatus" role="status">
                        Ready to plan your activity.
                    </div>
                </form>
            </div>

            <div class="results" id="results">
                <div class="empty-state">
                    <div class="big-icon">🌤️</div>
                    <h3>Your recommendations will appear here</h3>
                    <p>
                        Complete the planner and click
                        “Find best time” to retrieve live forecast data.
                    </p>
                </div>
            </div>
        </div>
    </div>
</section>

<section class="section" id="dashboard">
    <div class="container">
        <div class="section-heading">
            <span class="eyebrow">SMART OUTDOOR INSIGHTS</span>
            <h2>More than a weather forecast</h2>
            <p>
                GeoAI brings together different data points
                to help you make more informed outdoor plans.
            </p>
        </div>

        <div class="dashboard-grid">
            <div class="info-card">
                <div class="icon">🌡️</div>
                <h3>Weather awareness</h3>
                <p>
                    Use temperature, rain probability and wind
                    to compare outdoor conditions.
                </p>
            </div>

            <div class="info-card">
                <div class="icon">🍃</div>
                <h3>Air-quality insights</h3>
                <p>
                    Check available AQI information before
                    planning demanding outdoor exercise.
                </p>
            </div>

            <div class="info-card">
                <div class="icon">📍</div>
                <h3>Nearby discovery</h3>
                <p>
                    Explore mapped parks, sports facilities
                    and other outdoor places.
                </p>
            </div>
        </div>
    </div>
</section>

<section class="section map-section" id="map-section">
    <div class="container">
        <div class="section-heading">
            <span class="eyebrow">EXPLORE YOUR SURROUNDINGS</span>
            <h2>Find outdoor places around you</h2>
            <p>
                Search for a location in the planner first,
                then view nearby places on the map.
            </p>
        </div>

        <div id="map"></div>
        <div class="places-list" id="placesList"></div>

        <p class="disclaimer" style="margin-top:16px">
            Map and place information depend on external services
            and OpenStreetMap contributor data.
        </p>
    </div>
</section>

<section class="section" id="safety">
    <div class="container safety-grid">
        <div>
            <span class="eyebrow">YOUR WELLBEING MATTERS</span>
            <h2 style="font-size:38px;margin-bottom:16px">
                Smart planning.<br>Safer adventures.
            </h2>

            <p style="color:var(--muted)">
                Forecast-based recommendations are useful planning
                aids, but weather can change. Use local alerts and
                your judgement before heading outdoors.
            </p>

            <ul class="safety-list">
                <li>
                    <strong>🌡️ Check the temperature</strong>
                    <span>Carry water and avoid overexertion in extreme heat.</span>
                </li>

                <li>
                    <strong>🌧️ Watch for rain and storms</strong>
                    <span>Postpone activities during dangerous weather.</span>
                </li>

                <li>
                    <strong>🍃 Consider air quality</strong>
                    <span>Reduce strenuous outdoor exercise when pollution is high.</span>
                </li>

                <li>
                    <strong>🧭 Plan your route</strong>
                    <span>Check daylight, local conditions and emergency access.</span>
                </li>
            </ul>
        </div>

        <div class="safety-image">
            <img
                src="https://images.unsplash.com/photo-1551632811-561732d1e306?auto=format&fit=crop&w=1000&q=85"
                alt="Hikers exploring a mountain trail"
                onerror="this.style.display='none'">
        </div>
    </div>
</section>

<section class="section" id="assistant">
    <div class="container">
        <div class="section-heading">
            <span class="eyebrow">MEET YOUR OUTDOOR HELPER</span>
            <h2>Ask GeoAI 🌿</h2>
            <p>
                Ask a question about outdoor activity planning,
                weather or air quality.
            </p>
        </div>

        <div class="chat-box">
            <div class="chat-header">
                <h3>GeoAI Assistant</h3>
                <p>Outdoor activity planning assistant</p>
            </div>

            <div class="chat-messages" id="chatMessages">
                <div class="message bot">
                    Hi! 👋 I'm GeoAI. Ask me about running,
                    cycling, hiking, weather or air quality.
                </div>
            </div>

            <form class="chat-form" id="chatForm">
                <input
                    id="chatInput"
                    type="text"
                    placeholder="Ask GeoAI a question..."
                    maxlength="1000"
                    required>
                <button class="btn" type="submit">Send ↑</button>
            </form>
        </div>
    </div>
</section>

<section class="section">
    <div class="container">
        <div class="about-box">
            <h2>Technology meets the outdoors 🌎</h2>
            <p>
                GeoAI is a demonstration project that combines
                geolocation, forecast data, air-quality information
                and activity-specific scoring to help people plan
                outdoor activities. Recommendations are estimates,
                not official safety guarantees.
            </p>
        </div>
    </div>
</section>

<footer>
    <div class="container footer-inner">
        <div>
            <h3>GeoAI 🌿</h3>
            <p>Smarter planning for outdoor living.</p>
        </div>

        <div>
            <p>
                Data:
                <a href="https://open-meteo.com/" target="_blank">Open-Meteo</a>
                ·
                <a href="https://www.openstreetmap.org/" target="_blank">OpenStreetMap</a>
            </p>
            <p style="margin-top:8px">© 2026 GeoAI Project</p>
        </div>
    </div>
</footer>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>

<script>
const API = window.location.origin;

const dateInput = document.getElementById("activityDate");
const today = new Date();
const localToday = new Date(
    today.getTime() - today.getTimezoneOffset() * 60000
).toISOString().split("T")[0];

dateInput.min = localToday;
dateInput.value = localToday;

let map;
let markers = [];

function escapeHTML(value) {
    return String(value ?? "").replace(/[&<>"']/g, char => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;"
    })[char]);
}

function initMap() {
    if (typeof L === "undefined") {
        document.getElementById("map").innerHTML =
            "<p style='padding:20px'>Map library could not load. Check your internet connection.</p>";
        return;
    }

    map = L.map("map").setView([18.5204, 73.8567], 11);

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: "&copy; OpenStreetMap contributors"
    }).addTo(map);

    L.marker([18.5204, 73.8567])
        .addTo(map)
        .bindPopup("Pune, India — default map view");
}

function renderPlaces(places) {
    const list = document.getElementById("placesList");

    list.innerHTML = "";

    if (!places || places.length === 0) {
        list.innerHTML =
            "<div class='place-item'>No nearby places were returned. Try again later.</div>";
        return;
    }

    places.forEach(place => {
        const item = document.createElement("div");
        item.className = "place-item";

        item.innerHTML = `
            <strong>${escapeHTML(place.name)}</strong>
            <small>${escapeHTML(place.type)}</small>
        `;

        list.appendChild(item);
    });
}

function updateMap(location, places) {
    if (!map || !location) return;

    markers.forEach(marker => map.removeLayer(marker));
    markers = [];

    const center = [
        location.latitude,
        location.longitude
    ];

    map.setView(center, 12);

    markers.push(
        L.marker(center)
            .addTo(map)
            .bindPopup(escapeHTML(location.name))
    );

    (places || []).forEach(place => {
        if (place.latitude == null || place.longitude == null) return;

        const marker = L.marker([
            place.latitude,
            place.longitude
        ])
        .addTo(map)
        .bindPopup(escapeHTML(place.name));

        markers.push(marker);
    });

    renderPlaces(places);
}

function renderResults(data) {
    const results = document.getElementById("results");
    const weather = data.current_weather || {};
    const recommendation = data.current_recommendation || {};
    const air = data.air_quality || {};
    const aqi = air.current?.us_aqi;
    const daily = data.forecast || {};

    const score = recommendation.score ?? 0;

    const reasons = (recommendation.reasons || [])
        .map(reason => `<li>${escapeHTML(reason)}</li>`)
        .join("");

    const bestTimes = (data.best_times || []).map(slot => `
        <div class="time-row">
            <div>
                <strong>${escapeHTML(slot.time)}</strong>
                <small>
                    ${escapeHTML(slot.temperature)}°C ·
                    Rain ${escapeHTML(slot.rain_probability)}% ·
                    Wind ${escapeHTML(slot.wind_speed)} km/h
                </small>
            </div>
            <span class="badge">${escapeHTML(slot.score)}/100</span>
        </div>
    `).join("");

    const days = (daily.time || []).slice(0, 5).map((day, i) => `
        <div class="time-row">
            <div>
                <strong>${escapeHTML(day)}</strong>
                <small>
                    ${escapeHTML(daily.temperature_2m_min?.[i] ?? "—")}°C –
                    ${escapeHTML(daily.temperature_2m_max?.[i] ?? "—")}°C
                </small>
            </div>
            <span class="badge">
                Rain ${escapeHTML(daily.precipitation_probability_max?.[i] ?? "—")}%
            </span>
        </div>
    `).join("");

    results.innerHTML = `
        <div class="score-card">
            <span>YOUR CURRENT ACTIVITY SCORE</span>
            <div class="score-number">${escapeHTML(score)}<small>/100</small></div>
            <div class="score-track">
                <div class="score-fill" style="width:${Math.max(0,Math.min(100,score))}%"></div>
            </div>
            <h3>${escapeHTML(recommendation.status || "Recommendation")}</h3>
            <p>
                ${escapeHTML(data.activity?.icon || "🌿")}
                ${escapeHTML(data.activity?.name || "Outdoor activity")}
                · ${escapeHTML(data.location?.name || "")},
                ${escapeHTML(data.location?.country || "")}
            </p>
        </div>

        <div class="data-grid">
            <div class="data-tile">
                <small>🌡️ Temperature</small>
                <strong>${escapeHTML(weather.temperature_2m ?? "—")}°C</strong>
            </div>
            <div class="data-tile">
                <small>💨 Wind</small>
                <strong>${escapeHTML(weather.wind_speed_10m ?? "—")}</strong>
                <small>km/h</small>
            </div>
            <div class="data-tile">
                <small>🍃 US AQI</small>
                <strong>${escapeHTML(aqi ?? "N/A")}</strong>
            </div>
        </div>

        <div class="result-card">
            <h4>🌿 What the conditions mean</h4>
            <ul class="result-list">${reasons}</ul>
        </div>

        <div class="result-card">
            <h4>🕒 Best forecast hours</h4>
            ${
                bestTimes ||
                "<p class='disclaimer'>No forecast hours matched your selected date and time. Try another forecast date or choose Any time.</p>"
            }
        </div>

        <div class="result-card">
            <h4>📅 Daily forecast</h4>
            ${days || "<p class='disclaimer'>Daily forecast unavailable.</p>"}
        </div>

        <div class="result-card">
            <h4>📍 Nearby outdoor places</h4>
            <p class="disclaimer">
                ${data.nearby_places?.length || 0} places returned.
                See the map below.
            </p>
        </div>

        <p class="disclaimer">${escapeHTML(data.disclaimer || "")}</p>
    `;

    updateMap(data.location, data.nearby_places || []);

    setTimeout(() => {
        if (map) map.invalidateSize();
    }, 100);
}

document.getElementById("plannerForm").addEventListener("submit", async event => {
    event.preventDefault();

    const button = document.getElementById("planButton");
    const status = document.getElementById("plannerStatus");
    const results = document.getElementById("results");

    const payload = {
        location: document.getElementById("location").value.trim(),
        activity: document.getElementById("activity").value,
        activity_date: dateInput.value,
        preferred_time: document.getElementById("preferredTime").value
    };

    button.disabled = true;
    button.textContent = "⏳ Finding recommendations...";
    status.textContent = "Connecting to weather and air-quality services...";

    results.innerHTML = `
        <div class="empty-state">
            <div class="big-icon">🌤️</div>
            <h3>Gathering your outdoor insights...</h3>
            <p>Please wait while GeoAI retrieves forecast information.</p>
        </div>
    `;

    try {
        const response = await fetch(`${API}/api/recommend`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(payload)
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || "Could not generate recommendations.");
        }

        renderResults(data);
        status.textContent = "Recommendations loaded successfully.";

    } catch (error) {
        results.innerHTML = `
            <div class="result-card">
                <h3>⚠️ Could not load recommendations</h3>
                <p style="margin-top:12px;color:#698178">
                    ${escapeHTML(error.message)}
                </p>
                <p class="disclaimer" style="margin-top:12px">
                    Check that the backend is running and your internet
                    connection is working, then try again.
                </p>
            </div>
        `;

        status.textContent = "Request failed. Check the error above.";

    } finally {
        button.disabled = false;
        button.textContent = "✨ Find best time";
    }
});

function addMessage(message, sender) {
    const box = document.getElementById("chatMessages");
    const item = document.createElement("div");

    item.className = `message ${sender}`;
    item.textContent = message;

    box.appendChild(item);
    box.scrollTop = box.scrollHeight;
}

document.getElementById("chatForm").addEventListener("submit", async event => {
    event.preventDefault();

    const input = document.getElementById("chatInput");
    const message = input.value.trim();

    if (!message) return;

    addMessage(message, "user");
    input.value = "";

    try {
        const response = await fetch(`${API}/api/assistant`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                message,
                location: document.getElementById("location").value.trim() || "Pune, India"
            })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || "Assistant request failed.");
        }

        addMessage(data.reply, "bot");

    } catch (error) {
        addMessage(
            "I could not connect to the assistant. Please check that the backend is running.",
            "bot"
        );
    }
});

initMap();
</script>

</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        reload=False,
    )
