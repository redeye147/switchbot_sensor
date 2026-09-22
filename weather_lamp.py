#!/usr/bin/env python3
# coding: utf-8
"""天気予報に応じて SwitchBot スマート電球の色を変える。

電球の色を見るだけで、これからの天気が分かるようにする。

    黄     晴
    白     曇
    青     雨
    水色   雪

いつの天気を見せるかは時刻で決まる。

    6時〜21時   今いる6時間区切り (気象庁の降水確率の単位に合わせる)
    22時〜23時   次の朝 (6時〜12時)
    0時〜5時    消灯 (quiet_hours)

    ./venv/bin/python weather_lamp.py --show      # 電球に触れず予報と判定を表示
    ./venv/bin/python weather_lamp.py             # 予報を見て電球を点ける
    ./venv/bin/python weather_lamp.py --off       # 消す
    ./venv/bin/python weather_lamp.py --at 2026-09-22T23:30 --show   # 時刻を仮定

場所やしきい値は weather_lamp.json で変える。初回実行時に作られる。
"""
import argparse
import asyncio
import datetime
import json
import logging
import pathlib

import weather
import weather_jma
import weather_openmeteo

log = logging.getLogger("weather_lamp")

CONFIG_PATH = pathlib.Path(__file__).resolve().parent / "weather_lamp.json"

# 東京都大田区 = 東京都 (130000) の「東京地方」。気温の地点は「東京」。
# 府県予報区コードは次で調べられる:
#   https://www.jma.go.jp/bosai/common/const/area.json
DEFAULT_CONFIG = {
    "source": "jma",
    "area_code": "130000",
    "area_name": "東京地方",     # 一次細分区域。null なら先頭
    "temp_point": "東京",        # 気温の地点。null なら先頭
    # source を "open-meteo" にしたときだけ使う (大田区の緯度経度)
    "latitude": 35.561,
    "longitude": 139.716,
    "mac": "80:65:99:9d:ad:de",
    "night_hour": 22,            # この時刻以降は翌朝の天気を表示する
    "quiet_hours": [0, 6],       # この時間帯は消灯する (0時〜5時台)
    "morning": [6, 11],          # 「午前」とみなす時間帯 (6時〜11時台)
    "rain_probability": weather.RAIN_PROBABILITY,
    "rain_amount": weather.RAIN_AMOUNT,
    "jacket_temp": weather.JACKET_TEMP,
    "brightness": 80,
    "colors": {
        "sunny":  [255, 180, 0],     # 黄   晴
        "cloudy": [255, 255, 255],   # 白   曇
        "rain":   [0, 60, 255],      # 青   雨
        "snow":   [0, 220, 255],     # 水色 雪
    },
}


def load_config(path=CONFIG_PATH):
    """設定を読む。無ければ既定値で作る。"""
    if not path.exists():
        path.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        log.warning("設定ファイルを作りました: %s", path)
        log.warning("取得元 %s / エリア %s になっています。違う場合は書き換えてください",
                    DEFAULT_CONFIG["source"], DEFAULT_CONFIG["area_code"])
        return dict(DEFAULT_CONFIG)

    config = dict(DEFAULT_CONFIG)
    config.update(json.loads(path.read_text(encoding="utf-8")))
    return config


def get_summary(config, date, window):
    """設定された取得元から予報を取り、判定に使う形にして返す。"""
    source = str(config.get("source", "jma")).lower()
    if source in ("jma", "気象庁"):
        data = weather_jma.fetch(config["area_code"])
        summary = weather_jma.summarise(data, date, window,
                                        config.get("area_name"),
                                        config.get("temp_point"))
        return summary, data
    if source in ("open-meteo", "openmeteo"):
        data = weather_openmeteo.fetch(config["latitude"], config["longitude"])
        summary = weather_openmeteo.summarise(data, date, window,
                                              place=config.get("place"))
        return summary, data
    raise weather.WeatherError(
        f"source が不正です: {config.get('source')!r} (jma か open-meteo)")


COLOR_NAMES = {"sunny": "黄", "cloudy": "白", "rain": "青", "snow": "水色"}


def pick_color(kind, colors):
    """天気の種類から色を選ぶ。"""
    return colors[kind]


def advice(verdict):
    """傘・上着の要否を一言で返す。色とは別に、文字で補足する。

    判断材料が無いものを「不要」と言わないこと。気象庁の3日予報は夕方の発表で
    当日分の気温が落ち、週間予報は翌日以降しか持たないため、夕方に「今日」を
    見ると気温が欠ける。そのとき上着の要否は判断できない。
    """
    need, no_need, unknown = [], [], []

    if verdict["umbrella"]:
        need.append("傘")
    elif verdict["max_probability"] is None and not verdict["weather_text"]:
        unknown.append("傘")
    else:
        no_need.append("傘")

    if verdict["jacket"]:
        need.append("上着")
    elif verdict["min_temperature"] is None:
        unknown.append("上着")
    else:
        no_need.append("上着")

    parts = []
    if need:
        parts.append("・".join(need) + " が必要")
    if no_need:
        parts.append(("・".join(no_need) + " は不要") if need or unknown
                     else "傘も上着も不要")
    if unknown:
        parts.append("・".join(unknown) + " は判断できません（予報に値がありません）")
    return "。".join(parts)


def describe(config, verdict, kind, label):
    """人が読む形にまとめる。"""
    place = verdict.get("place") or config.get("area_code") or "?"
    lines = [
        f"{verdict['source']}  {place}  {label}  ({verdict['date']})",
        "",
        f"  天気        : {weather.WEATHER_NAMES[kind]}  -> {COLOR_NAMES[kind]}",
    ]
    if verdict.get("weather_text"):
        lines.append(f"  予報文      : {verdict['weather_text']}")
    lines += [
        f"  降水確率最大: {_unit(verdict['max_probability'], '%')}",
        f"  最高気温    : {_unit(verdict['max_temperature'], '度')}",
        f"  最低気温    : {_unit(verdict['min_temperature'], '度')}",
    ]
    if verdict["total_precipitation"] is not None:
        lines.append(f"  降水量合計  : {verdict['total_precipitation']}mm")
    lines += ["", f"  持ち物      : {advice(verdict)}"]
    for reason in verdict["reasons"]:
        lines.append(f"    - {reason}")
    return "\n".join(lines)



def _unit(value, unit):
    return "不明" if value is None else f"{value}{unit}"


def detail_table(verdict):
    """時間帯ごとの内訳。--show で使う。取得元により列が違う。"""
    rows = verdict.get("detail") or []
    if not rows:
        return "  (内訳なし)"
    has_temp = any("temperature" in r for r in rows)
    out = ["  時間帯      降水確率" + ("   気温    降水量" if has_temp else "")]
    for r in rows:
        line = f"  {r['label']:10} {_unit(r.get('probability'), '%'):>7}"
        if has_temp:
            line += f"  {_unit(r.get('temperature'), '度'):>7} {_unit(r.get('precipitation'), 'mm'):>8}"
        out.append(line)
    return "\n".join(out)


async def set_lamp(mac, rgb, brightness, attempts=3):
    """電球を点けて色を変える。switchbot_control を使う。"""
    import switchbot_control as ctl

    packet = ctl.build_command("rgb", level=brightness, rgb=tuple(rgb))
    resp = await ctl.send_command(mac, packet, attempts=attempts)
    return ctl.describe_response(resp)


async def turn_off(mac, attempts=3):
    import switchbot_control as ctl

    resp = await ctl.send_command(mac, ctl.build_command("off"), attempts=attempts)
    return ctl.describe_response(resp)


async def main():
    ap = argparse.ArgumentParser(description="天気に応じて SwitchBot 電球の色を変える")
    ap.add_argument("--show", action="store_true",
                    help="電球に触れず、予報と判定だけ表示する")
    ap.add_argument("--raw", action="store_true",
                    help="取得した予報の中身をそのまま表示する (解析の確認用)")
    ap.add_argument("--off", action="store_true", help="電球を消して終了")
    ap.add_argument("--at", help="この時刻として判断する (確認用)。例 2026-09-22T23:30")
    ap.add_argument("--dry-run", action="store_true",
                    help="判定はするが電球には送らない (送信内容は表示する)")
    ap.add_argument("--config", type=pathlib.Path, default=CONFIG_PATH)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_config(args.config)

    if args.off:
        print(await turn_off(config["mac"]))
        return

    now = datetime.datetime.fromisoformat(args.at) if args.at else datetime.datetime.now()
    quiet = config.get("quiet_hours")
    sleeping = weather.in_quiet_hours(now, quiet)
    date, window, label = weather.target_period(
        now, config.get("night_hour", 22), tuple(config.get("morning", [6, 11])))

    if sleeping and not (args.show or args.raw):
        # 予報を取りに行く必要はない。消すだけ。
        log.info("%d時〜%d時台は消灯します", quiet[0], (quiet[1] - 1) % 24)
        try:
            print(await turn_off(config["mac"]))
        except Exception as e:
            log.error("電球を消せませんでした: %s", e)
            raise SystemExit(1)
        return

    try:
        summary, data = get_summary(config, date, window)
    except weather.WeatherError as e:
        log.error("%s", e)
        raise SystemExit(1)

    if args.raw:
        if str(config.get("source", "jma")).lower() in ("jma", "気象庁"):
            print(weather_jma.outline(data))
        else:
            print(json.dumps(data, ensure_ascii=False, indent=2)[:4000])
        print()

    verdict = weather.decide(summary, config["rain_probability"],
                             config["rain_amount"], config["jacket_temp"])
    kind = weather.classify(summary, config["rain_probability"])
    rgb = pick_color(kind, config["colors"])

    print(describe(config, verdict, kind, label))
    if sleeping:
        print()
        print(f"  ※ この時刻は消灯時間帯です ({quiet[0]}時〜{(quiet[1] - 1) % 24}時台)。"
              "通常実行では電球を消します")

    if args.show:
        print()
        print(detail_table(verdict))
        return

    print()
    if args.dry_run:
        import switchbot_control as ctl
        packet = ctl.build_command("rgb", level=config["brightness"], rgb=tuple(rgb))
        print(f"送信するパケット: {packet.hex()}  (RGB {rgb} 明るさ {config['brightness']}%)")
        return

    try:
        print(await set_lamp(config["mac"], rgb, config["brightness"]))
    except Exception as e:
        log.error("電球に送れませんでした: %s", e)
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
