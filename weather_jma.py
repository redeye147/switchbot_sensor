#!/usr/bin/env python3
# coding: utf-8
"""気象庁の予報 JSON を読む。

気象庁は防災情報のページで使っている JSON をそのまま公開している。
APIキーも登録も不要。

    https://www.jma.go.jp/bosai/forecast/data/forecast/{府県コード}.json

応答は要素2つの配列で、

    [0] 3日予報   timeSeries が3本
          [0] weatherCodes / weathers / winds / waves   … 日ごと
          [1] pops (降水確率)                            … 6時間ごと
          [2] temps (気温)                               … 00時=最低 09時=最高
    [1] 週間予報  timeSeries が2本
          [0] weatherCodes / pops / reliabilities
          [1] tempsMin / tempsMax

気温は 3日予報の方が細かいが、発表時刻によっては当日分が入っていない。
その場合は週間予報の tempsMin / tempsMax を使う。

降水の有無は予報文 (「くもり 時々 雨」など) に 雨・雪・雷 が含まれるかで見る。
天気コードの上1桁では判定できない。たとえば 202「くもり一時雨」は 2 で始まる
のに雨を含む。
"""
import json
import urllib.request

import weather

FORECAST_URL = "https://www.jma.go.jp/bosai/forecast/data/forecast/{area}.json"
SOURCE_NAME = "気象庁"

# 予報文にこれが含まれれば降水ありとみなす
WET_WORDS = ("雨", "雪", "雷", "みぞれ", "ひょう")


def fetch(area_code, timeout=20, url=FORECAST_URL):
    """府県予報区のコード (愛知県なら 230000) で予報を取る。"""
    try:
        request = urllib.request.Request(
            url.format(area=area_code),
            headers={"User-Agent": "switchbot_sensor weather lamp"})
        with urllib.request.urlopen(request, timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8"))
    except Exception as e:
        raise WeatherErrorFor(area_code, e) from e


def WeatherErrorFor(area_code, e):
    return weather.WeatherError(
        f"気象庁の予報を取得できませんでした (エリア {area_code}): {e}")


def _day(stamp):
    """"2026-09-22T06:00:00+09:00" -> "2026-09-22" """
    return str(stamp)[:10]


def _hour(stamp):
    """"2026-09-22T06:00:00+09:00" -> 6"""
    try:
        return int(str(stamp)[11:13])
    except ValueError:
        return None


def _pick_area(series, area_name=None):
    """timeSeries から対象の区域を選ぶ。名前指定が無ければ先頭。"""
    areas = series.get("areas") or []
    if not areas:
        return None
    if area_name:
        for a in areas:
            if (a.get("area") or {}).get("name") == area_name:
                return a
    return areas[0]


def _number(value):
    """"22" -> 22.0 / "" や None -> None"""
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _series_with(block, key):
    """指定した項目を持つ timeSeries を返す。"""
    for series in block.get("timeSeries") or []:
        for area in series.get("areas") or []:
            if key in area:
                return series
    return None


def summarise(data, date, window=weather.WINDOW, area_name=None, temp_point=None):
    """気象庁の応答から、判定に必要な値だけ取り出す。"""
    if not isinstance(data, list) or not data:
        raise weather.WeatherError("気象庁の応答が想定した形ではありません")

    near = data[0]
    weekly = data[1] if len(data) > 1 else {}
    place = None

    summary = weather.empty_summary(SOURCE_NAME, place, date)
    start, end = window

    # --- 予報文と天気コード ---
    series = _series_with(near, "weathers") or _series_with(near, "weatherCodes")
    if series:
        area = _pick_area(series, area_name)
        if area:
            summary["place"] = (area.get("area") or {}).get("name")
            for i, stamp in enumerate(series.get("timeDefines") or []):
                if _day(stamp) != date:
                    continue
                texts = area.get("weathers") or []
                if i < len(texts):
                    summary["weather_text"] = " ".join(str(texts[i]).split())
                break

    if summary["weather_text"]:
        summary["has_precipitation"] = any(w in summary["weather_text"]
                                           for w in WET_WORDS)

    # --- 降水確率 (6時間ごと) ---
    series = _series_with(near, "pops")
    if series:
        area = _pick_area(series, area_name)
        pops = (area or {}).get("pops") or []
        best = None
        for i, stamp in enumerate(series.get("timeDefines") or []):
            if _day(stamp) != date or i >= len(pops):
                continue
            hour = _hour(stamp)
            if hour is None:
                continue
            # 6時間区切りの開始時刻。時間帯と少しでも重なれば見る。
            if hour + 5 < start or hour > end:
                continue
            value = _number(pops[i])
            if value is None:
                continue
            label = f"{hour}-{hour + 6}時"
            summary["detail"].append({"label": label, "probability": int(value)})
            best = value if best is None else max(best, value)
            if value >= weather.RAIN_PROBABILITY:
                summary["wet_periods"].append(label)
        if best is not None:
            summary["max_probability"] = int(best)

    # --- 気温 ---
    lows, highs = _temperatures(near, date, area_name or temp_point, temp_point)
    if not lows and not highs:
        lows, highs = _weekly_temperatures(weekly, date, temp_point)
    if lows:
        summary["min_temperature"] = min(lows)
    if highs:
        summary["max_temperature"] = max(highs)

    return summary


def _temperatures(near, date, _unused, temp_point):
    """3日予報の temps から、その日の最低・最高を拾う。

    timeDefines の 00時が最低、09時が最高。発表時刻によっては当日分が無い。
    """
    series = _series_with(near, "temps")
    if not series:
        return [], []
    area = _pick_area(series, temp_point)
    temps = (area or {}).get("temps") or []

    lows, highs = [], []
    for i, stamp in enumerate(series.get("timeDefines") or []):
        if _day(stamp) != date or i >= len(temps):
            continue
        value = _number(temps[i])
        if value is None:
            continue
        hour = _hour(stamp)
        (lows if hour is not None and hour < 9 else highs).append(value)
    return lows, highs


def _weekly_temperatures(weekly, date, temp_point):
    """週間予報の tempsMin / tempsMax から拾う。3日予報に無い日の予備。"""
    if not weekly:
        return [], []
    series = _series_with(weekly, "tempsMin") or _series_with(weekly, "tempsMax")
    if not series:
        return [], []
    area = _pick_area(series, temp_point)
    mins = (area or {}).get("tempsMin") or []
    maxs = (area or {}).get("tempsMax") or []

    lows, highs = [], []
    for i, stamp in enumerate(series.get("timeDefines") or []):
        if _day(stamp) != date:
            continue
        low = _number(mins[i]) if i < len(mins) else None
        high = _number(maxs[i]) if i < len(maxs) else None
        if low is not None:
            lows.append(low)
        if high is not None:
            highs.append(high)
    return lows, highs


def outline(data):
    """応答の構造を人が読める形で返す。--raw で使う。"""
    lines = []
    for bi, block in enumerate(data if isinstance(data, list) else []):
        lines.append(f"[{bi}] {block.get('publishingOffice', '?')} "
                     f"発表 {block.get('reportDatetime', '?')}")
        for si, series in enumerate(block.get("timeSeries") or []):
            times = series.get("timeDefines") or []
            lines.append(f"    timeSeries[{si}]  時刻 {len(times)}個: "
                         f"{times[0] if times else '-'} ...")
            for area in series.get("areas") or []:
                name = (area.get("area") or {}).get("name", "?")
                code = (area.get("area") or {}).get("code", "?")
                keys = [k for k in area if k != "area"]
                lines.append(f"        {name} ({code}): {', '.join(keys)}")
                for k in keys:
                    lines.append(f"            {k} = {area[k]}")
    return "\n".join(lines)
