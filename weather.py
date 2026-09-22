#!/usr/bin/env python3
# coding: utf-8
"""天気予報の取得と、傘・上着の要否判定。

API は Open-Meteo (https://open-meteo.com/)。APIキー不要・無料で、時間別の
降水確率と気温が取れる。追加ライブラリも不要 (標準の urllib で足りる)。

判定の考え方:
  傘   … 出かけている時間帯のどこかで雨が降りそうなら必要。朝が晴れでも夜に
          降るなら持って出る必要があるため、朝だけでなく帰宅時刻までを見る。
  上着 … 同じ時間帯の最低気温で決める。昼が暖かくても朝晩が冷えるなら要る。
"""
import json
import urllib.parse
import urllib.request

API_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY_FIELDS = ["temperature_2m", "precipitation_probability", "precipitation",
                 "weathercode"]

# 既定のしきい値
RAIN_PROBABILITY = 50      # 降水確率 % これ以上で傘
RAIN_AMOUNT = 1.0          # 降水量 mm 合計がこれ以上でも傘
JACKET_TEMP = 18.0         # 最低気温 度 これ以下で上着
WINDOW = (6, 21)           # 見る時間帯 (6時〜21時)

# 天気コード (WMO) のうち、雨や雪を表すもの。降水確率が低くても降るなら傘。
# 参考: https://open-meteo.com/en/docs (WMO Weather interpretation codes)
PRECIPITATION_CODES = set(range(51, 68)) | set(range(71, 87)) | {80, 81, 82, 95, 96, 99}


class WeatherError(RuntimeError):
    """予報を取得できなかった。"""


def fetch(latitude, longitude, timezone="Asia/Tokyo", timeout=20, url=API_URL):
    """Open-Meteo から予報を取る。失敗したら WeatherError。"""
    query = urllib.parse.urlencode({
        "latitude": latitude,
        "longitude": longitude,
        "hourly": ",".join(HOURLY_FIELDS),
        "timezone": timezone,
        "forecast_days": 2,
    })
    try:
        with urllib.request.urlopen(f"{url}?{query}", timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8"))
    except Exception as e:
        raise WeatherError(f"予報を取得できませんでした: {e}") from e


def hours_for(data, date, window=WINDOW):
    """指定した日付の、指定した時間帯のデータを取り出す。

    date は "2026-09-22" 形式。Open-Meteo は timezone を指定すると
    "2026-09-22T06:00" のような現地時刻の文字列を返す。
    """
    hourly = data.get("hourly")
    if not hourly or "time" not in hourly:
        raise WeatherError("予報に hourly が含まれていません")

    start, end = window
    rows = []
    for i, stamp in enumerate(hourly["time"]):
        day, _, clock = stamp.partition("T")
        if day != date:
            continue
        hour = int(clock[:2])
        if not start <= hour <= end:
            continue
        rows.append({
            "hour": hour,
            "temperature": _at(hourly, "temperature_2m", i),
            "probability": _at(hourly, "precipitation_probability", i),
            "precipitation": _at(hourly, "precipitation", i),
            "code": _at(hourly, "weathercode", i),
        })
    if not rows:
        raise WeatherError(f"{date} の {start}時〜{end}時 のデータがありません")
    return rows


def _at(hourly, field, i):
    """欠けている項目や null があっても落ちないように読む。"""
    values = hourly.get(field)
    if not values or i >= len(values):
        return None
    return values[i]


def decide(rows, rain_probability=RAIN_PROBABILITY, rain_amount=RAIN_AMOUNT,
           jacket_temp=JACKET_TEMP):
    """傘と上着の要否を決める。判断の根拠も返す。"""
    probs = [r["probability"] for r in rows if r["probability"] is not None]
    temps = [r["temperature"] for r in rows if r["temperature"] is not None]
    rain = [r["precipitation"] for r in rows if r["precipitation"] is not None]
    codes = [r["code"] for r in rows if r["code"] is not None]

    max_prob = max(probs) if probs else None
    total_rain = round(sum(rain), 1) if rain else None
    min_temp = min(temps) if temps else None
    max_temp = max(temps) if temps else None
    wet_code = any(c in PRECIPITATION_CODES for c in codes)

    reasons = []
    umbrella = False
    if max_prob is not None and max_prob >= rain_probability:
        umbrella = True
        reasons.append(f"降水確率が最大 {max_prob}% ({rain_probability}% 以上)")
    if total_rain is not None and total_rain >= rain_amount:
        umbrella = True
        reasons.append(f"降水量の合計が {total_rain}mm ({rain_amount}mm 以上)")
    if wet_code:
        umbrella = True
        reasons.append("予報に雨または雪が含まれる")

    jacket = False
    if min_temp is not None and min_temp <= jacket_temp:
        jacket = True
        reasons.append(f"最低気温が {min_temp}度 ({jacket_temp}度 以下)")

    # 何時ごろ降るかが分かると判断しやすい
    wet_hours = [r["hour"] for r in rows
                 if (r["probability"] or 0) >= rain_probability
                 or (r["precipitation"] or 0) > 0]

    return {
        "umbrella": umbrella,
        "jacket": jacket,
        "max_probability": max_prob,
        "total_precipitation": total_rain,
        "min_temperature": min_temp,
        "max_temperature": max_temp,
        "wet_hours": wet_hours,
        "reasons": reasons,
    }
