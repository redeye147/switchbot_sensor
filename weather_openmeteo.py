#!/usr/bin/env python3
# coding: utf-8
"""Open-Meteo の予報を読む。予備の取得元。

気象庁より粒度は細かい (1時間ごと・降水量あり) が、日本の予報精度では
気象庁のほうが信頼できるため既定ではない。weather_lamp.json の source を
"open-meteo" にすると使う。
"""
import json
import urllib.parse
import urllib.request

import weather

API_URL = "https://api.open-meteo.com/v1/forecast"
SOURCE_NAME = "Open-Meteo"
HOURLY_FIELDS = ["temperature_2m", "precipitation_probability", "precipitation",
                 "weathercode"]

# WMO の天気コードのうち雨・雪・雷を表すもの
PRECIPITATION_CODES = set(range(51, 68)) | set(range(71, 87)) | {80, 81, 82, 95, 96, 99}


def fetch(latitude, longitude, timezone="Asia/Tokyo", timeout=20, url=API_URL):
    query = urllib.parse.urlencode({
        "latitude": latitude, "longitude": longitude,
        "hourly": ",".join(HOURLY_FIELDS),
        "timezone": timezone, "forecast_days": 2,
    })
    try:
        with urllib.request.urlopen(f"{url}?{query}", timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8"))
    except Exception as e:
        raise weather.WeatherError(f"Open-Meteo から取得できませんでした: {e}") from e


def _at(hourly, field, i):
    values = hourly.get(field)
    if not values or i >= len(values):
        return None
    return values[i]


def summarise(data, date, window=weather.WINDOW, place=None):
    hourly = (data or {}).get("hourly")
    if not hourly or "time" not in hourly:
        raise weather.WeatherError("Open-Meteo の応答に hourly がありません")

    start, end = window
    summary = weather.empty_summary(SOURCE_NAME, place, date)
    probs, temps, rain, codes = [], [], [], []

    for i, stamp in enumerate(hourly["time"]):
        day, _, clock = str(stamp).partition("T")
        if day != date:
            continue
        hour = int(clock[:2])
        if not start <= hour <= end:
            continue
        p = _at(hourly, "precipitation_probability", i)
        t = _at(hourly, "temperature_2m", i)
        r = _at(hourly, "precipitation", i)
        c = _at(hourly, "weathercode", i)
        summary["detail"].append({"label": f"{hour}時", "probability": p,
                                  "temperature": t, "precipitation": r})
        if p is not None:
            probs.append(p)
        if t is not None:
            temps.append(t)
        if r is not None:
            rain.append(r)
        if c is not None:
            codes.append(c)
        if (p or 0) >= weather.RAIN_PROBABILITY or (r or 0) > 0:
            summary["wet_periods"].append(f"{hour}時")

    if not summary["detail"]:
        raise weather.WeatherError(f"{date} の {start}時〜{end}時 のデータがありません")

    summary["max_probability"] = max(probs) if probs else None
    summary["total_precipitation"] = round(sum(rain), 1) if rain else None
    summary["min_temperature"] = min(temps) if temps else None
    summary["max_temperature"] = max(temps) if temps else None
    summary["has_precipitation"] = any(c in PRECIPITATION_CODES for c in codes)
    return summary
