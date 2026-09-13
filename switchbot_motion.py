#!/usr/bin/env python3
# coding: utf-8
"""SwitchBot 人感センサー BLE スキャナ (bleak 版)

接続せずにアドバタイズを受信するだけなので、ペアリングもクラウド連携も不要です。

  ./venv/bin/python switchbot_motion.py --mac d2:dc:22:fd:6d:d1
  ./venv/bin/python switchbot_motion.py --scan      # MAC を調べる
  ./venv/bin/python switchbot_motion.py --mac ... --raw   # 生データを見る
"""
import argparse
import asyncio
import logging
import time

# bleak を import する前に、システムの dist-packages を排除する (sitefix.py 参照)
from sitefix import strip_dist_packages

_STRIPPED = strip_dist_packages()

from bleak import BleakScanner  # noqa: E402

from switchbot_protocol import (  # noqa: E402
    DEVICE_TYPE_MOTION,
    battery_note,
    LIGHT_LEVEL_NAMES,
    SENSING_DISTANCE_NAMES,
    parse_motion,
)
from switchbot_contact import discover_switchbots, print_raw  # noqa: E402

log = logging.getLogger("switchbot")


class MotionSensor:
    """人感センサーのアドバタイズを受信し続ける。"""

    def __init__(self, macaddr, on_update=None, on_raw=None):
        self.macaddr = macaddr.lower()
        self.on_update = on_update
        self.on_raw = on_raw
        self.state = None

    def _handle(self, device, adv):
        if device.address.lower() != self.macaddr:
            return

        if self.on_raw:
            self.on_raw(adv)

        for payload in adv.service_data.values():
            data = parse_motion(payload)
            if data is None:
                continue
            data["rssi"] = adv.rssi
            self.state = data
            if self.on_update:
                self.on_update(data)
            return

    async def run(self):
        while True:
            try:
                async with BleakScanner(self._handle):
                    await asyncio.Future()      # 永久に受信し続ける
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("BLE スキャンが停止しました (%s) -- 5秒後に再開します", e)
                await asyncio.sleep(5)


# 状態とみなすキー。secSincePir と rssi は常時動くので差分判定から除く。
STATE_KEYS = ("isMotion", "lightLevel", "ledEnabled", "iotEnabled", "sensingDistance")


class StatePrinter:
    """既定では状態が変わったときだけ表示する (アドバタイズは毎秒届くため)。"""

    def __init__(self, show_all=False):
        self.show_all = show_all
        self.prev = None

    def __call__(self, data):
        key = tuple(data[k] for k in STATE_KEYS)
        changed = key != self.prev
        self.prev = key

        if not (self.show_all or changed):
            return

        ts = time.strftime("%H:%M:%S")
        print("-----------", ts + (" (変化)" if changed else ""))
        print("人感 (PIR)    :", "動きあり" if data["isMotion"] else "動きなし")
        print("最後の検知から:", data["secSincePir"], "秒")
        print("明るさ        :", LIGHT_LEVEL_NAMES[data["lightLevel"]],
              f'(isIlluminance={data["isIlluminance"]})')
        print("検知距離      :", SENSING_DISTANCE_NAMES[data["sensingDistance"]])
        print("LED / IoT     :", f'{data["ledEnabled"]} / {data["iotEnabled"]}')
        print("バッテリー    :", f'{data["battery"]} %' + battery_note(data["battery"]))
        print("rssi          :", data["rssi"])
        print("-----------", flush=True)

        # ここに取得データによるアクションを記述
        # if data["isMotion"]:
        #     ...


async def main():
    ap = argparse.ArgumentParser(description="SwitchBot 人感センサー BLE スキャナ")
    ap.add_argument("--mac", help="対象デバイスの MAC アドレス (例 d2:dc:22:fd:6d:d1)")
    ap.add_argument("--scan", action="store_true", help="周囲の SwitchBot を探して MAC を表示して終了")
    ap.add_argument("--all", action="store_true",
                    help="状態が変わらなくても毎回表示する (既定は変化時のみ)")
    ap.add_argument("--raw", action="store_true",
                    help="サービスデータを生のまま表示する (バイト配置の検証用)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.scan:
        await discover_switchbots(highlight=DEVICE_TYPE_MOTION)
        return
    if not args.mac:
        ap.error("--mac を指定するか、--scan で MAC アドレスを調べてください")

    if _STRIPPED:
        log.warning("システムの dist-packages を import パスから除外しました: %s",
                    ", ".join(_STRIPPED))

    log.info("%s の受信を開始します (Ctrl-C で終了)", args.mac.lower())
    if not args.all and not args.raw:
        log.info("状態が変わったときだけ表示します (毎回見るには --all)")

    sensor = MotionSensor(
        args.mac,
        on_update=None if args.raw else StatePrinter(show_all=args.all),
        on_raw=print_raw if args.raw else None,
    )
    await sensor.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
