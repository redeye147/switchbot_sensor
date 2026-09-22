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


print("=== 天気の分類と色 (晴・曇・雨・雪) ===")
def kind_of(**kw):
    summary = jma.summarise(jma_payload(**kw), DATE)
    return weather.classify(summary), summary

cases = [
    ("晴れ",           dict(weathers=("晴れ", "晴れ"), pops=("0",)*4),          "sunny"),
    ("くもり",         dict(weathers=("くもり", "くもり"), pops=("20",)*4),     "cloudy"),
    ("雨",             dict(weathers=("雨", "雨"), pops=("80",)*4),             "rain"),
    ("雪",             dict(weathers=("雪", "雪"), pops=("80",)*4),             "snow"),
    ("みぞれ",         dict(weathers=("みぞれ", "雪"), pops=("70",)*4),         "snow"),
    ("雷雨",           dict(weathers=("くもり 所により 雷を伴い 雨", "くもり"),
                            pops=("90",)*4),                                    "rain"),
]
for name, kw, want in cases:
    kind, _ = kind_of(**kw)
    rgb = wl.pick_color(kind, wl.DEFAULT_CONFIG["colors"])
    print(f"   {name:12} -> {weather.WEATHER_NAMES[kind]} ({wl.COLOR_NAMES[kind]}) {rgb}")
    assert kind == want, (name, kind, want)

print()
print("=== 予報文がその日全体でも、時間帯の降水確率を優先する ===")
# 「くもり昼過ぎから雨」の朝。文には雨があるが、朝の確率は10%
summary = jma.summarise(jma_payload(weathers=("くもり 昼過ぎ から 雨", "晴れ"),
                                    pops=("0", "10", "70", "60")), DATE, window=(6, 11))
kind = weather.classify(summary)
print(f"   朝 (6-12時, 確率{summary['max_probability']}%) -> {weather.WEATHER_NAMES[kind]}")
assert kind == "cloudy", "予報文の「雨」だけで朝まで雨にしてはいけない"

summary = jma.summarise(jma_payload(weathers=("くもり 昼過ぎ から 雨", "晴れ"),
                                    pops=("0", "10", "70", "60")), DATE, window=(12, 17))
kind = weather.classify(summary)
print(f"   昼 (12-18時, 確率{summary['max_probability']}%) -> {weather.WEATHER_NAMES[kind]}")
assert kind == "rain"

print()
print("=== 時刻によって、いつの天気を見せるか変わる ===")
import datetime
expect = [(0, "今日の午前"), (5, "今日の午前"), (7, "今日 6時〜12時"),
          (13, "今日 12時〜18時"), (20, "今日 18時〜24時"),
          (22, "明日の午前"), (23, "明日の午前")]
for hour, want_label in expect:
    d, w, label = weather.target_period(datetime.datetime(2026, 9, 22, hour, 30))
    print(f"   {hour:2}時 -> {label:16} {d} {w}")
    assert label == want_label, (hour, label)
    if hour >= 22:
        assert d == "2026-09-23", "22時以降は翌日を見なければならない"
    else:
        assert d == "2026-09-22"
    if "午前" in label:
        # 終端を 12 にすると 12時始まりの区切りまで入ってしまう
        assert w[1] < 12, f"午前なのに {w} を対象にしている"

print()
print("=== 傘・上着の判定は文字の補足として残す ===")
# 202「くもり一時雨」は 2 で始まるが雨を含む。天気コードの上1桁では判定できない。
for text, wet in (("くもり 一時 雨", True), ("くもり 時々 晴れ", False),
                  ("雪 のち くもり", True), ("晴れ 時々 くもり", False)):
    v = judge_jma(weathers=(text, "晴れ"), pops=("0", "0", "0", "0"), temps=("22", "30"))
    print(f"   {text:20} -> {wl.advice(v)}")
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
print("=== 判断材料が無いものを「不要」と言わない ===")
# 実機で起きた例: 17時発表の予報を19時台に見ると、当日の気温が落ちている
def verdict_with(**kw):
    summary = weather.empty_summary("気象庁", "東京地方", DATE)
    summary.update(kw)
    return weather.decide(summary)

no_temp = verdict_with(max_probability=10, weather_text="晴れ 夜遅く くもり")
text = wl.advice(no_temp)
print(f"   気温なし  -> {text}")
assert "上着" in text and "判断できません" in text
assert "上着 は不要" not in text and "傘も上着も不要" not in text

warm = verdict_with(max_probability=10, weather_text="晴れ", min_temperature=22.0)
print(f"   気温あり  -> {wl.advice(warm)}")
assert wl.advice(warm) == "傘も上着も不要"

nothing = verdict_with()
print(f"   全部不明  -> {wl.advice(nothing)}")
assert "傘・上着 は判断できません（予報に値がありません）" == wl.advice(nothing)

both = verdict_with(max_probability=80, weather_text="雨", min_temperature=10.0)
print(f"   雨で寒い  -> {wl.advice(both)}")
assert wl.advice(both) == "傘・上着 が必要"

print()
print("=== Open-Meteo も同じ判定に乗る ===")
flat = lambda v: [v] * 24
data = {"hourly": {
    "time": [f"{DATE}T{h:02d}:00" for h in range(24)],
    "temperature_2m": flat(12), "precipitation_probability": flat(80),
    "precipitation": flat(0.0), "weathercode": flat(61)}}
summary = om.summarise(data, DATE)
v = weather.decide(summary)
kind = weather.classify(summary)
print(f"   {weather.WEATHER_NAMES[kind]} ({wl.COLOR_NAMES[kind]}) / {wl.advice(v)}")
assert kind == "rain" and v["source"] == "Open-Meteo"

print()
print("=== 設定の取得元が不正なら分かるエラーにする ===")
try:
    wl.get_summary({"source": "yahoo"}, DATE, (6, 21))
    raise AssertionError("不正な source で例外が出ていない")
except weather.WeatherError as e:
    print(f"   {e}")

print()
print("すべて OK")
