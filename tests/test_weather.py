#!/usr/bin/env python3
# coding: utf-8
"""天気の判定ロジックを、通信せずに検証する。

Open-Meteo のレスポンス形式を模した固定データを使う。実際の API との
突き合わせは `./venv/bin/python weather_lamp.py --show` で行うこと。

実行:
    ./venv/bin/python tests/test_weather.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import weather
import weather_lamp as wl

DATE = "2026-09-22"


def payload(temps, probs, rain, codes=None, date=DATE):
    """Open-Meteo 形式の応答を組み立てる。0時から1時間おき。"""
    return {
        "hourly": {
            "time": [f"{date}T{h:02d}:00" for h in range(len(temps))],
            "temperature_2m": list(temps),
            "precipitation_probability": list(probs),
            "precipitation": list(rain),
            "weathercode": list(codes or [0] * len(temps)),
        }
    }


def judge(temps, probs, rain, codes=None, window=(6, 21)):
    rows = weather.hours_for(payload(temps, probs, rain, codes), DATE, window)
    return weather.decide(rows)


flat = lambda v: [v] * 24

print("=== 4通りの判定と色 ===")
cases = [
    ("暖かく晴れ",       flat(25), flat(10), flat(0.0), "none"),
    ("寒いが晴れ",       flat(12), flat(10), flat(0.0), "jacket"),
    ("暖かいが雨",       flat(25), flat(80), flat(0.0), "umbrella"),
    ("寒くて雨",         flat(12), flat(80), flat(0.0), "both"),
]
for name, t, p, r, want in cases:
    v = judge(t, p, r)
    key, rgb = wl.pick_color(v, wl.DEFAULT_CONFIG["colors"])
    print(f"   {name:12} 傘={int(v['umbrella'])} 上着={int(v['jacket'])} "
          f"-> {wl.COLOR_NAMES[key]} {rgb}")
    assert key == want, (name, key, want)

print()
print("=== 朝は晴れでも、帰宅時間に降るなら傘 ===")
# 6〜16時は降水確率0、17時以降だけ80%
probs = [0] * 17 + [80] * 7
v = judge(flat(25), probs, flat(0.0))
print(f"   降りそうな時間: {v['wet_hours']}  傘={int(v['umbrella'])}")
assert v["umbrella"], "夜の雨を見落としている"
assert v["wet_hours"] == [17, 18, 19, 20, 21]

# 同じ予報でも、朝だけしか見なければ傘は不要になる
morning = weather.decide(weather.hours_for(payload(flat(25), probs, flat(0.0)), DATE, (6, 9)))
assert not morning["umbrella"]
print("   朝だけの時間帯なら傘不要 (時間帯の設定が効いている)")

print()
print("=== 昼が暖かくても朝晩が冷えるなら上着 ===")
temps = [10] * 9 + [26] * 8 + [10] * 7      # 朝晩10度、昼26度
v = judge(temps, flat(0), flat(0.0))
print(f"   最低 {v['min_temperature']}度 / 最高 {v['max_temperature']}度  上着={int(v['jacket'])}")
assert v["jacket"] and v["min_temperature"] == 10 and v["max_temperature"] == 26

print()
print("=== 降水確率が低くても、降水量や天気コードがあれば傘 ===")
v = judge(flat(25), flat(20), [0.0] * 12 + [0.3] * 12)     # 合計 3.6mm
print(f"   降水量合計 {v['total_precipitation']}mm  傘={int(v['umbrella'])}")
assert v["umbrella"]

v = judge(flat(25), flat(20), flat(0.0), codes=flat(61))   # 61 = 雨
print(f"   天気コード 61 (雨)  傘={int(v['umbrella'])}")
assert v["umbrella"]

v = judge(flat(25), flat(20), flat(0.0), codes=flat(3))    # 3 = くもり
print(f"   天気コード 3 (くもり)  傘={int(v['umbrella'])}")
assert not v["umbrella"]

print()
print("=== 値が欠けていても落ちない ===")
broken = payload(flat(25), flat(10), flat(0.0))
broken["hourly"]["precipitation_probability"] = [None] * 24
broken["hourly"]["temperature_2m"][8] = None
rows = weather.hours_for(broken, DATE)
v = weather.decide(rows)
print(f"   降水確率が全て null -> max_probability={v['max_probability']}")
assert v["max_probability"] is None and v["min_temperature"] == 25

# 項目そのものが無い応答
missing = {"hourly": {"time": [f"{DATE}T{h:02d}:00" for h in range(24)]}}
v = weather.decide(weather.hours_for(missing, DATE))
assert v["umbrella"] is False and v["jacket"] is False
print("   項目が無い応答でも判定は返る (すべて不明扱い)")

print()
print("=== 予報に無い日付や壊れた応答はエラーにする ===")
for bad, label in (({}, "hourly なし"),
                   ({"hourly": {}}, "time なし")):
    try:
        weather.hours_for(bad, DATE)
        raise AssertionError(f"{label} で例外が出ていない")
    except weather.WeatherError as e:
        print(f"   {label}: {e}")

try:
    weather.hours_for(payload(flat(25), flat(0), flat(0.0)), "2099-01-01")
    raise AssertionError("無い日付で例外が出ていない")
except weather.WeatherError as e:
    print(f"   無い日付: {e}")

print()
print("=== 設定ファイルの色は4通りそろっている ===")
for key in ("none", "jacket", "umbrella", "both"):
    rgb = wl.DEFAULT_CONFIG["colors"][key]
    assert len(rgb) == 3 and all(0 <= c <= 255 for c in rgb), (key, rgb)
print("   ", {k: wl.DEFAULT_CONFIG["colors"][k] for k in wl.COLOR_NAMES})

print()
print("すべて OK")
