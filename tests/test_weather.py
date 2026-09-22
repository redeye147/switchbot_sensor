#!/usr/bin/env python3
# coding: utf-8
"""天気の解析と判定を、通信せずに検証する。

気象庁の応答形式を模した固定データを使う。**実際の応答との突き合わせは
ラズパイ側で `weather_lamp.py --raw` を実行して行うこと。** 開発環境からは
気象庁にも Open-Meteo にも接続できないため、形式は公開仕様に基づく。

実行:
    ./venv/bin/python tests/test_weather.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import weather
import weather_jma as jma
import weather_openmeteo as om
import weather_lamp as wl

DATE = "2026-09-22"
NEXT = "2026-09-23"


def jma_payload(weathers=("くもり", "晴れ"), pops=("10", "10", "20", "10"),
                temps=("22", "30"), weekly_min=None, weekly_max=None,
                area="西部", point="名古屋"):
    """気象庁の forecast JSON を模す。

    pops は当日の 6時間ごと (0,6,12,18時始まり)。
    temps は timeDefines と 1:1 で、00時=最低 09時=最高。
    """
    near = {
        "publishingOffice": "名古屋地方気象台",
        "reportDatetime": f"{DATE}T05:00:00+09:00",
        "timeSeries": [
            {"timeDefines": [f"{DATE}T05:00:00+09:00", f"{NEXT}T00:00:00+09:00"],
             "areas": [{"area": {"name": area, "code": "230010"},
                        "weatherCodes": ["201", "101"],
                        "weathers": list(weathers),
                        "winds": ["北の風", "北の風"]}]},
            {"timeDefines": [f"{DATE}T{h:02d}:00:00+09:00" for h in (0, 6, 12, 18)],
             "areas": [{"area": {"name": area, "code": "230010"},
                        "pops": list(pops)}]},
            {"timeDefines": [f"{DATE}T00:00:00+09:00", f"{DATE}T09:00:00+09:00"],
             "areas": [{"area": {"name": point, "code": "51106"},
                        "temps": list(temps)}]},
        ],
    }
    weekly = {"timeSeries": [
        {"timeDefines": [f"{DATE}T00:00:00+09:00", f"{NEXT}T00:00:00+09:00"],
         "areas": [{"area": {"name": "愛知県", "code": "230000"},
                    "weatherCodes": ["201", "101"], "pops": ["", "20"]}]},
        {"timeDefines": [f"{DATE}T00:00:00+09:00", f"{NEXT}T00:00:00+09:00"],
         "areas": [{"area": {"name": point, "code": "51106"},
                    "tempsMin": list(weekly_min or ["", "18"]),
                    "tempsMax": list(weekly_max or ["", "27"])}]},
    ]}
    return [near, weekly]


def judge_jma(**kw):
    summary = jma.summarise(jma_payload(**kw), DATE)
    return weather.decide(summary)


print("=== 気象庁: 4通りの判定と色 ===")
cases = [
    ("暖かく晴れ", dict(weathers=("晴れ", "晴れ"), pops=("0", "0", "10", "0"),
                        temps=("22", "30")), "none"),
    ("寒いが晴れ", dict(weathers=("晴れ", "晴れ"), pops=("0", "0", "10", "0"),
                        temps=("12", "20")), "jacket"),
    ("暖かいが雨", dict(weathers=("雨", "くもり"), pops=("30", "70", "80", "40"),
                        temps=("22", "30")), "umbrella"),
    ("寒くて雨",   dict(weathers=("雨", "くもり"), pops=("30", "70", "80", "40"),
                        temps=("12", "18")), "both"),
]
for name, kw, want in cases:
    v = judge_jma(**kw)
    key, rgb = wl.pick_color(v, wl.DEFAULT_CONFIG["colors"])
    print(f"   {name:12} 傘={int(v['umbrella'])} 上着={int(v['jacket'])} "
          f"-> {wl.COLOR_NAMES[key]} {rgb}")
    assert key == want, (name, key, want)

print()
print("=== 予報文で降水を判定する (天気コードの上1桁では判定できない) ===")
# 202「くもり一時雨」は 2 で始まるが雨を含む
for text, wet in (("くもり 一時 雨", True), ("くもり 時々 晴れ", False),
                  ("雪 のち くもり", True), ("晴れ 時々 くもり", False),
                  ("くもり 所により 雷を伴い 激しい雨", True)):
    v = judge_jma(weathers=(text, "晴れ"), pops=("0", "0", "0", "0"), temps=("22", "30"))
    print(f"   {text:28} -> 傘={int(v['umbrella'])}")
    assert v["umbrella"] == wet, text

print()
print("=== 朝は晴れでも、帰宅時間に降るなら傘 ===")
# 0-6時と6-12時は低く、18-24時だけ 80%
v = judge_jma(weathers=("くもり", "晴れ"), pops=("0", "10", "20", "80"), temps=("22", "30"))
print(f"   降りそうな時間: {v['wet_periods']}  傘={int(v['umbrella'])}")
assert v["umbrella"] and v["wet_periods"] == ["18-24時"]

# 朝だけの時間帯設定なら、夜の雨は対象外
morning = weather.decide(jma.summarise(
    jma_payload(weathers=("くもり", "晴れ"), pops=("0", "10", "20", "80"),
                temps=("22", "30")), DATE, window=(6, 9)))
print(f"   6〜9時だけ見た場合: 傘={int(morning['umbrella'])} "
      f"内訳={[d['label'] for d in morning['detail']]}")
assert not morning["umbrella"]

print()
print("=== 6時間区切りは時間帯と重なれば拾う ===")
# 6-12時の区切りは、窓が 9時からでも重なるので対象
v = weather.decide(jma.summarise(
    jma_payload(pops=("0", "90", "0", "0"), weathers=("くもり", "晴れ"),
                temps=("22", "30")), DATE, window=(9, 21)))
assert v["umbrella"], "重なっている区切りを取りこぼしている"
print(f"   窓9〜21時 で 6-12時の区切りを拾えた: {v['wet_periods']}")

print()
print("=== 気温は 00時=最低 09時=最高 として読む ===")
v = judge_jma(temps=("14", "28"), weathers=("晴れ", "晴れ"), pops=("0",)*4)
print(f"   最低 {v['min_temperature']}度 / 最高 {v['max_temperature']}度")
assert v["min_temperature"] == 14.0 and v["max_temperature"] == 28.0

print()
print("=== 3日予報に当日の気温が無ければ週間予報で補う ===")
payload = jma_payload(temps=("", ""), weekly_min=["11", "18"], weekly_max=["19", "27"])
v = weather.decide(jma.summarise(payload, DATE))
print(f"   週間予報から 最低 {v['min_temperature']}度 / 最高 {v['max_temperature']}度")
assert v["min_temperature"] == 11.0 and v["jacket"]

print()
print("=== 値が空や欠損でも落ちない ===")
payload = jma_payload(pops=("", "", "", ""), temps=("", ""))
payload[1] = {}                                     # 週間予報ごと無い
v = weather.decide(jma.summarise(payload, DATE))
print(f"   降水確率={v['max_probability']} 最低気温={v['min_temperature']} "
      f"傘={int(v['umbrella'])} 上着={int(v['jacket'])}")
assert v["max_probability"] is None and v["min_temperature"] is None
assert not v["jacket"]

print()
print("=== 応答が想定外ならエラーにする ===")
for bad, label in (({}, "配列でない"), ([], "空の配列")):
    try:
        jma.summarise(bad, DATE)
        raise AssertionError(f"{label} で例外が出ていない")
    except weather.WeatherError as e:
        print(f"   {label}: {e}")

print()
print("=== 構造の表示 (--raw) が読める形になっている ===")
text = jma.outline(jma_payload())
assert "名古屋地方気象台" in text and "pops" in text and "temps" in text
print("\n".join("   " + l for l in text.split("\n")[:6]))

print()
print("=== Open-Meteo も同じ判定に乗る ===")
flat = lambda v: [v] * 24
data = {"hourly": {
    "time": [f"{DATE}T{h:02d}:00" for h in range(24)],
    "temperature_2m": flat(12), "precipitation_probability": flat(80),
    "precipitation": flat(0.0), "weathercode": flat(61)}}
v = weather.decide(om.summarise(data, DATE))
key, _ = wl.pick_color(v, wl.DEFAULT_CONFIG["colors"])
print(f"   傘={int(v['umbrella'])} 上着={int(v['jacket'])} -> {wl.COLOR_NAMES[key]}")
assert key == "both" and v["source"] == "Open-Meteo"

print()
print("=== 設定の取得元が不正なら分かるエラーにする ===")
try:
    wl.get_summary({"source": "yahoo", "window": [6, 21]}, DATE)
    raise AssertionError("不正な source で例外が出ていない")
except weather.WeatherError as e:
    print(f"   {e}")

print()
print("すべて OK")
