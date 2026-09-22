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

log = logging.getLogger("weather_lamp")

CONFIG_PATH = pathlib.Path(__file__).resolve().parent / "weather_lamp.json"

# 名古屋。別の場所なら weather_lamp.json を書き換える。
DEFAULT_CONFIG = {
    "place": "名古屋",
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
        log.warning("場所が %s (%s, %s) になっています。違う場合は書き換えてください",
                    DEFAULT_CONFIG["place"], DEFAULT_CONFIG["latitude"],
                    DEFAULT_CONFIG["longitude"])
        return dict(DEFAULT_CONFIG)

    config = dict(DEFAULT_CONFIG)
    config.update(json.loads(path.read_text(encoding="utf-8")))
    return config


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


def describe(config, date, rows, verdict, key):
    """人が読む形にまとめる。"""
    lines = [
        f"{config['place']} ({config['latitude']}, {config['longitude']})  {date}",
        f"対象時間帯: {config['window'][0]}時〜{config['window'][1]}時",
        "",
        f"  最高気温    : {verdict['max_temperature']}度",
        f"  最低気温    : {verdict['min_temperature']}度",
        f"  降水確率最大: {verdict['max_probability']}%",
        f"  降水量合計  : {verdict['total_precipitation']}mm",
    ]
    if verdict["wet_hours"]:
        hours = ", ".join(f"{h}時" for h in verdict["wet_hours"])
        lines.append(f"  降りそうな時間: {hours}")
    lines += ["", f"  判定: {LABELS[key]} -> {COLOR_NAMES[key]}"]
    for reason in verdict["reasons"]:
        lines.append(f"    - {reason}")
    if not verdict["reasons"]:
        lines.append("    - しきい値を超える要素なし")
    return "\n".join(lines)


def hourly_table(rows):
    """時間ごとの内訳。--show で使う。"""
    out = ["  時刻   気温   降水確率   降水量"]
    for r in rows:
        out.append(f"  {r['hour']:2}時  {r['temperature']:5}度  "
                   f"{r['probability']:5}%   {r['precipitation']:5}mm")
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
        data = weather.fetch(config["latitude"], config["longitude"])
        rows = weather.hours_for(data, date, tuple(config["window"]))
    except weather.WeatherError as e:
        log.error("%s", e)
        raise SystemExit(1)

    verdict = weather.decide(rows, config["rain_probability"],
                             config["rain_amount"], config["jacket_temp"])
    key, rgb = pick_color(verdict, config["colors"])

    print(describe(config, date, rows, verdict, key))

    if args.show:
        print()
        print(hourly_table(rows))
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
