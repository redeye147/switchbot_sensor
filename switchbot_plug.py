#!/usr/bin/env python3
# coding: utf-8
"""SwitchBot プラグミニ BLE スキャナ (bleak 版)

プラグミニは Wi-Fi 機器ですが、状態を BLE でもアドバタイズしています。本スクリプト
はそれを受信するだけなので、クラウド連携もトークンも不要です (ON/OFF の操作はでき
ません。受信専用です)。

他機種と違い、プラグミニは **サービスデータではなくメーカー固有データ** に状態を
載せます (switchbot_protocol.parse_plug() の説明を参照)。

  ./venv/bin/python switchbot_plug.py --scan             # 周囲のプラグミニを探す
  ./venv/bin/python switchbot_plug.py --mac d8:3b:da:25:d9:e6
  ./venv/bin/python switchbot_plug.py --watch            # 見つかった全台をまとめて監視
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
    DEVICE_TYPE_PLUG_JP,
    SWITCHBOT_COMPANY_ID,
    parse_plug,
)
from switchbot_contact import discover_switchbots, print_raw  # noqa: E402

log = logging.getLogger("switchbot")


class PlugMini:
    """プラグミニのアドバタイズを受信し続ける。

    macs に MAC を列挙すると、その台のみを対象にする。空なら受信できた
    プラグミニをすべて対象にする (--watch)。
    """

    def __init__(self, macs=(), on_update=None, on_raw=None):
        self.macs = {m.lower() for m in macs}
        self.on_update = on_update
        self.on_raw = on_raw
        self.states = {}

    def _handle(self, device, adv):
        addr = device.address.lower()
        if self.macs and addr not in self.macs:
            return

        payload = adv.manufacturer_data.get(SWITCHBOT_COMPANY_ID)
        if payload is None:
            return

        data = parse_plug(payload)
        if data is None:
            return

        if self.on_raw:
            self.on_raw(adv)
            return

        data["address"] = addr
        data["rssi"] = adv.rssi
        self.states[addr] = data
        if self.on_update:
            self.on_update(data)

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


# 状態とみなすキー。sequence と rssi と消費電力は常時動くので差分判定から除く。
STATE_KEYS = ("isOn", "isOverload", "hasDelay", "hasTimer", "utcSynced")


class StatePrinter:
    """既定では状態が変わったときだけ表示する。台ごとに前回値を覚える。

    消費電力は常に微動するため、そのままだと毎パケット表示になってしまう。
    power_delta_w 以上動いたときだけ変化とみなす。
    """

    def __init__(self, show_all=False, power_delta_w=1.0):
        self.show_all = show_all
        self.power_delta_w = power_delta_w
        self.prev = {}

    def __call__(self, data):
        addr = data["address"]
        key = tuple(data[k] for k in STATE_KEYS)
        prev_key, prev_power = self.prev.get(addr, (None, None))

        changed = key != prev_key
        if prev_power is not None and abs(data["powerW"] - prev_power) >= self.power_delta_w:
            changed = True
        self.prev[addr] = (key, data["powerW"])

        if not (self.show_all or changed):
            return

        ts = time.strftime("%H:%M:%S")
        print("-----------", f'{ts} {addr}' + (" (変化)" if changed else ""))
        unknown = "" if data["stateByte"] in (0x00, 0x80) else f'  (未知の値 0x{data["stateByte"]:02x})'
        print("電源          :", ("ON" if data["isOn"] else "OFF") + unknown)
        print("消費電力      :", f'{data["powerW"]} W  (生値 {data["powerRaw"]})')
        print("過負荷        :", "！15A 超過" if data["isOverload"] else "なし")
        print("タイマー/遅延 :", f'{data["hasTimer"]} / {data["hasDelay"]}')
        print("UTC 同期      :", data["utcSynced"])
        print("Wi-Fi rssi    :", data["wifiRssi"])
        print("BLE rssi      :", data["rssi"])
        print("-----------", flush=True)

        # ここに取得データによるアクションを記述
        # if data["powerW"] > 1000:
        #     ...


async def find_plugs(seconds=10):
    """周囲のプラグミニを列挙する。"""
    found = {}

    def cb(device, adv):
        payload = adv.manufacturer_data.get(SWITCHBOT_COMPANY_ID)
        if payload is None:
            return
        data = parse_plug(payload)
        if data:
            found[device.address.lower()] = (data, adv.rssi)

    async with BleakScanner(cb):
        await asyncio.sleep(seconds)

    print(f"--- {seconds}秒スキャン結果: プラグミニ {len(found)}台 ---")
    for addr, (d, rssi) in sorted(found.items()):
        state = "ON " if d["isOn"] else "OFF"
        print(f"  {addr}  {state}  {d['powerW']:7.1f} W  wifi_rssi={d['wifiRssi']:4}  ble_rssi={rssi:4}")
    if not found:
        print("  見つかりませんでした。プラグミニが通電しているか確認してください。")
    return sorted(found)


async def main():
    ap = argparse.ArgumentParser(description="SwitchBot プラグミニ BLE スキャナ")
    ap.add_argument("--mac", action="append", default=[],
                    help="対象の MAC アドレス。複数台は --mac を繰り返す")
    ap.add_argument("--watch", action="store_true",
                    help="MAC を指定せず、受信できたプラグミニをすべて監視する")
    ap.add_argument("--scan", action="store_true", help="周囲のプラグミニを探して終了")
    ap.add_argument("--devices", action="store_true",
                    help="プラグミニに限らず周囲の SwitchBot 機器を一覧表示して終了")
    ap.add_argument("--all", action="store_true",
                    help="状態が変わらなくても毎回表示する (既定は変化時のみ)")
    ap.add_argument("--raw", action="store_true",
                    help="メーカー固有データを生のまま表示する (バイト配置の検証用)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.devices:
        await discover_switchbots(highlight=DEVICE_TYPE_PLUG_JP)
        return
    if args.scan:
        await find_plugs()
        return
    if not args.mac and not args.watch:
        ap.error("--mac を指定するか、--watch で全台を監視、--scan で MAC を調べてください")

    if _STRIPPED:
        log.warning("システムの dist-packages を import パスから除外しました: %s",
                    ", ".join(_STRIPPED))

    target = ", ".join(m.lower() for m in args.mac) if args.mac else "受信できた全台"
    log.info("%s の受信を開始します (Ctrl-C で終了)", target)
    if not args.all and not args.raw:
        log.info("状態が変わったときだけ表示します (毎回見るには --all)")

    plug = PlugMini(
        args.mac,
        on_update=None if args.raw else StatePrinter(show_all=args.all),
        on_raw=print_raw if args.raw else None,
    )
    await plug.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
