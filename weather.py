#!/usr/bin/env python3
# coding: utf-8
"""傘・上着の要否判定と、予報取得元の共通部分。

取得元ごとの違いは summary という辞書に吸収し、判定はそこだけを見る。

    weather_jma.py        気象庁 (既定)
    weather_openmeteo.py  Open-Meteo (予備)

summary の形:
    source              取得元の名前
    place               場所の表示名
    date                対象日 "YYYY-MM-DD"
    max_probability     降水確率の最大 % (不明なら None)
    total_precipitation 降水量の合計 mm (取得元が出さなければ None)
    min_temperature     最低気温 度 (不明なら None)
    max_temperature     最高気温 度 (不明なら None)
    weather_text        「くもり 時々 雨」のような予報文 (無ければ None)
    has_precipitation   予報文や天気コードが雨・雪を示すか
    wet_periods         降りそうな時間帯の表示用リスト
    detail              時間ごとの内訳 (--show 用)
"""

# 表示する天気の種類
SUNNY, CLOUDY, RAIN, SNOW = "sunny", "cloudy", "rain", "snow"
WEATHER_NAMES = {SUNNY: "晴", CLOUDY: "曇", RAIN: "雨", SNOW: "雪"}

# 気象庁の降水確率は6時間区切り
BLOCK_HOURS = 6

# 既定のしきい値
RAIN_PROBABILITY = 50      # 降水確率 % これ以上で傘
RAIN_AMOUNT = 1.0          # 降水量 mm 合計がこれ以上でも傘
JACKET_TEMP = 18.0         # 最低気温 度 これ以下で上着
WINDOW = (6, 21)           # 見る時間帯 (6時〜21時)


class WeatherError(RuntimeError):
    """予報を取得または解釈できなかった。"""


def empty_summary(source, place, date):
    return {
        "source": source, "place": place, "date": date,
        "max_probability": None, "total_precipitation": None,
        "min_temperature": None, "max_temperature": None,
        "weather_text": None, "has_precipitation": False,
        "wet_periods": [], "detail": [],
    }


def decide(summary, rain_probability=RAIN_PROBABILITY, rain_amount=RAIN_AMOUNT,
           jacket_temp=JACKET_TEMP):
    """傘と上着の要否を決める。判断の根拠も返す。

    傘   … 対象の時間帯に降りそうなら必要。
    上着 … 同じ時間帯の最低気温で決める。昼が暖かくても朝晩が冷えるなら要る。

    降水確率が分かっていればそれを優先し、予報文だけで覆さない。classify()
    (色の決定) と同じ根拠にするため。気象庁の予報文はその日全体を表すので、
    「くもり所により朝晩雨」を確率20%の時間帯にまで適用すると、色は曇なのに
    持ち物は傘、という食い違いが起きる。

    確率が低いのに予報文が雨に触れている場合は、覆さずに notes で伝える。
    """
    reasons = []
    notes = []
    umbrella = False

    prob = summary["max_probability"]
    amount = summary["total_precipitation"]
    wet_text = summary["has_precipitation"]
    text = summary["weather_text"]

    if prob is not None:
        if prob >= rain_probability:
            umbrella = True
            reasons.append(f"降水確率が最大 {prob}% ({rain_probability}% 以上)")
        elif wet_text:
            notes.append(f"予報文に雨や雪がありますが（{text}）、"
                         f"この時間帯の降水確率は {prob}% です")
    elif wet_text:
        umbrella = True
        reasons.append(f"予報に雨または雪が含まれる（{text}）" if text
                       else "予報に雨または雪が含まれる")

    # 降水量は確率と別の根拠。出る取得元 (Open-Meteo) でのみ使う。
    if amount is not None and amount >= rain_amount:
        umbrella = True
        reasons.append(f"降水量の合計が {amount}mm ({rain_amount}mm 以上)")

    jacket = False
    low = summary["min_temperature"]
    if low is not None and low <= jacket_temp:
        jacket = True
        reasons.append(f"最低気温が {low}度 ({jacket_temp}度 以下)")

    verdict = dict(summary)
    verdict.update({"umbrella": umbrella, "jacket": jacket,
                    "reasons": reasons, "notes": notes})
    return verdict


def target_period(now, night_hour=22, morning=(6, 11)):
    """いつの天気を表示するかを決める。

    夜遅くに今日の残りを見せても意味がないので、22時以降は翌朝に切り替える。
    日付が変わってから朝までも同じ扱い (その日の朝を見せる)。
    日中は、気象庁の降水確率に合わせて、今いる6時間区切りを対象にする。

    「午前」は 6〜11時。終端を 12 にすると、12時始まりの区切りまで拾ってしまう。

    戻り値は (対象日 "YYYY-MM-DD", 時間帯 (開始, 終了), 表示用のラベル)。
    """
    import datetime

    if now.hour >= night_hour:
        tomorrow = now.date() + datetime.timedelta(days=1)
        return tomorrow.strftime("%Y-%m-%d"), morning, "明日の午前"
    if now.hour < morning[0]:
        return now.strftime("%Y-%m-%d"), morning, "今日の午前"

    start = (now.hour // BLOCK_HOURS) * BLOCK_HOURS
    end = start + BLOCK_HOURS
    return now.strftime("%Y-%m-%d"), (start, end - 1), f"今日 {start}時〜{end}時"


def in_quiet_hours(now, quiet):
    """消灯する時間帯かどうか。

    quiet は (開始時, 終了時)。終了時は含まない。[0, 6] なら 0時〜5時台。
    [22, 6] のように日をまたぐ指定もできる。
    """
    if not quiet:
        return False
    start, end = quiet
    if start == end:
        return False
    hour = now.hour
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end      # 日をまたぐ


def classify(summary, rain_probability=RAIN_PROBABILITY):
    """晴・曇・雨・雪のどれかに分類する。

    降水確率が分かっていればそれを優先する。気象庁の予報文はその日全体を
    表すので、「くもり昼過ぎから雨」の朝の時間帯まで雨にしないため。
    予報文は、降るときに雨か雪かを分け、降らないときに晴か曇かを分けるのに使う。
    """
    text = summary.get("weather_text") or ""
    snowing = any(w in text for w in ("雪", "みぞれ"))
    prob = summary.get("max_probability")

    if prob is not None:
        if prob >= rain_probability:
            return SNOW if snowing else RAIN
    elif snowing:
        return SNOW
    elif any(w in text for w in ("雨", "雷")):
        return RAIN

    if "晴" in text:
        return SUNNY
    if text:
        return CLOUDY
    # 予報文が無いときは降水確率だけで決める
    return CLOUDY if prob is None or prob >= 30 else SUNNY
