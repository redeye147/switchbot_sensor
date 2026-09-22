#!/usr/bin/env python3
# coding: utf-8
"""天気予報に応じて SwitchBot スマート電球の色を変える。

朝、電球の色を見るだけで「傘がいるか」「上着がいるか」が分かるようにする。

    緑     傘も上着も不要
    橙     上着だけ必要 (寒い)
    青     傘だけ必要 (降りそう)
    紫     傘も上着も必要

    ./venv/bin/python weather_lamp.py --show      # 電球に触れず予報と判定を表示
    ./venv/bin/python weather_lamp.py             # 予報を見て電球を点ける
    ./venv/bin/python weather_lamp.py --off       # 消す

場所やしきい値は weather_lamp.json で変える。初回実行時に作られる。
"""
import argparse
import asyncio
import json
import logging
import pathlib
import time

import weather
import weather_jma
import weather_openmeteo

log = logging.getLogger("weather_lamp")

CONFIG_PATH = pathlib.Path(__file__).resolve().parent / "weather_lamp.json"

# 愛知県。別の場所なら weather_lamp.json を書き換える。
# 気象庁の府県予報区コードは次で調べられる:
#   https://www.jma.go.jp/bosai/common/const/area.json
DEFAULT_CONFIG = {
    "source": "jma",
    "area_code": "230000",
    "area_name": None,          # 一次細分区域。null なら先頭 (愛知県なら「西部」)
    "temp_point": None,         # 気温の地点。null なら先頭 (愛知県なら「名古屋」)
    # source を "open-meteo" にしたときだけ使う
    "latitude": 35.18,
    "longitude": 136.91,
    "mac": "80:65:99:9d:ad:de",
    "window": [6, 21],
    "rain_probability": weather.RAIN_PROBABILITY,
    "rain_amount": weather.RAIN_AMOUNT,
    "jacket_temp": weather.JACKET_TEMP,
    "brightness": 80,
    "colors": {
        "none":     [0, 200, 60],     # 緑   何も要らない
        "jacket":   [255, 110, 0],    # 橙   上着だけ
        "umbrella": [0, 80, 255],     # 青   傘だけ
        "both":     [160, 0, 255],    # 紫   両方
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


def get_summary(config, date):
    """設定された取得元から予報を取り、判定に使う形にして返す。"""
    source = str(config.get("source", "jma")).lower()
    if source in ("jma", "気象庁"):
        data = weather_jma.fetch(config["area_code"])
        summary = weather_jma.summarise(data, date, tuple(config["window"]),
                                        config.get("area_name"),
                                        config.get("temp_point"))
        return summary, data
    if source in ("open-meteo", "openmeteo"):
        data = weather_openmeteo.fetch(config["latitude"], config["longitude"])
        summary = weather_openmeteo.summarise(data, date, tuple(config["window"]),
                                              place=config.get("place"))
        return summary, data
    raise weather.WeatherError(
        f"source が不正です: {config.get('source')!r} (jma か open-meteo)")


def pick_color(verdict, colors):
    """判定から色を選ぶ。"""
    if verdict["umbrella"] and verdict["jacket"]:
        return "both", colors["both"]
    if verdict["umbrella"]:
        return "umbrella", colors["umbrella"]
    if verdict["jacket"]:
        return "jacket", colors["jacket"]
    return "none", colors["none"]


LABELS = {
    "none": "傘も上着も不要",
    "jacket": "上着が必要",
    "umbrella": "傘が必要",
    "both": "傘と上着が必要",
}
COLOR_NAMES = {"none": "緑", "jacket": "橙", "umbrella": "青", "both": "紫"}


def describe(config, verdict, key):
    """人が読む形にまとめる。"""
    place = verdict.get("place") or config.get("area_code") or "?"
    lines = [
        f"{verdict['source']}  {place}  {verdict['date']}",
        f"対象時間帯: {config['window'][0]}時〜{config['window'][1]}時",
        "",
    ]
    if verdict.get("weather_text"):
        lines.append(f"  予報        : {verdict['weather_text']}")
    lines += [
        f"  最高気温    : {_unit(verdict['max_temperature'], '度')}",
        f"  最低気温    : {_unit(verdict['min_temperature'], '度')}",
        f"  降水確率最大: {_unit(verdict['max_probability'], '%')}",
    ]
    if verdict["total_precipitation"] is not None:
        lines.append(f"  降水量合計  : {verdict['total_precipitation']}mm")
    if verdict["wet_periods"]:
        lines.append(f"  降りそうな時間: {', '.join(verdict['wet_periods'])}")

    lines += ["", f"  判定: {LABELS[key]} -> {COLOR_NAMES[key]}"]
    for reason in verdict["reasons"]:
        lines.append(f"    - {reason}")
    if not verdict["reasons"]:
        lines.append("    - しきい値を超える要素なし")
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
    ap.add_argument("--date", help="対象日 (既定: 今日)。例 2026-09-23")
    ap.add_argument("--dry-run", action="store_true",
                    help="判定はするが電球には送らない (送信内容は表示する)")
    ap.add_argument("--config", type=pathlib.Path, default=CONFIG_PATH)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_config(args.config)

    if args.off:
        print(await turn_off(config["mac"]))
        return

    date = args.date or time.strftime("%Y-%m-%d")

    try:
        summary, data = get_summary(config, date)
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
    key, rgb = pick_color(verdict, config["colors"])

    print(describe(config, verdict, key))

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
