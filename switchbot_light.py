#!/usr/bin/env python3
# coding: utf-8
"""SwitchBot スマート電球 / テープライト BLE スキャナ (bleak 版)

どちらも Wi-Fi 機器ですが、状態を BLE でもアドバタイズしています。本スクリプトは
それを受信するだけなので、クラウド連携もトークンも不要です。**点灯/消灯や色の変更
はできません** (受信専用)。

プラグミニと同じく、状態は **サービスデータではなくメーカー固有データ** に入ります。

  ./venv/bin/python switchbot_light.py --scan      # 周囲のランプを探す
  ./venv/bin/python switchbot_light.py --watch     # 見つかった全台を監視
  ./venv/bin/python switchbot_light.py --mac AA:BB:CC:DD:EE:FF --raw

※ 手元に実機が無いため未検証です。値がおかしい場合は --raw の出力をもとに
   バイト配置を見直してください。
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
    BULB_LIGHT_STATE_NAMES,
    DEVICE_TYPE_BULB,
    DEVICE_TYPE_BULB_PAIRING,
    DEVICE_TYPE_NAMES,
    DEVICE_TYPE_STRIP,
    DEVICE_TYPE_STRIP_PAIRING,
    NETWORK_STATUS_NAMES,
    STRIP_MODE_NAMES,
    SWITCHBOT_COMPANY_ID,
    parse_bulb,
    parse_strip,
    service_device_type,
)
from switchbot_contact import discover_switchbots, print_raw  # noqa: E402

log = logging.getLogger("switchbot")

BULB_TYPES = {DEVICE_TYPE_BULB, DEVICE_TYPE_BULB_PAIRING}
STRIP_TYPES = {DEVICE_TYPE_STRIP, DEVICE_TYPE_STRIP_PAIRING}


def parse_light(adv):
    """アドバタイズからランプの状態を読む。ランプでなければ None。

    メーカー固有データは SwitchBot 全機種が同じ company ID で出すため、機種の
    判別はサービスデータの機種バイトで行う。
    """
    dtype = service_device_type(adv.service_data)
    payload = adv.manufacturer_data.get(SWITCHBOT_COMPANY_ID)
    if payload is None:
        return None

    if dtype in BULB_TYPES:
        data = parse_bulb(payload)
        kind = "電球"
    elif dtype in STRIP_TYPES:
        data = parse_strip(payload)
        kind = "テープライト"
    else:
        return None

    if data is None:
        return None
    data["kind"] = kind
    data["deviceType"] = dtype
    return data


class Light:
    """ランプのアドバタイズを受信し続ける。macs が空なら全台が対象。"""

    def __init__(self, macs=(), on_update=None, on_raw=None):
        self.macs = {m.lower() for m in macs}
        self.on_update = on_update
        self.on_raw = on_raw
        self.states = {}

    def _handle(self, device, adv):
        addr = device.address.lower()
        if self.macs and addr not in self.macs:
            return

        data = parse_light(adv)
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


# 状態とみなすキー。sequence と rssi は常時動くので差分判定から除く。
STATE_KEYS = ("isOn", "brightness", "networkStatus")


class StatePrinter:
    """既定では状態が変わったときだけ表示する。台ごとに前回値を覚える。"""

    def __init__(self, show_all=False):
        self.show_all = show_all
        self.prev = {}

    def __call__(self, data):
        addr = data["address"]
        extra = data.get("lightState", data.get("mode"))
        key = tuple(data[k] for k in STATE_KEYS) + (extra, str(data.get("colors")))
        changed = key != self.prev.get(addr)
        self.prev[addr] = key

        if not (self.show_all or changed):
            return

        ts = time.strftime("%H:%M:%S")
        print("-----------", f'{ts} {addr} ({data["kind"]})' + (" (変化)" if changed else ""))
        print("電源          :", "点灯" if data["isOn"] else "消灯")
        print("明るさ        :", f'{data["brightness"]} %')
        if data["kind"] == "電球":
            print("点灯モード    :", BULB_LIGHT_STATE_NAMES.get(data["lightState"], f'? ({data["lightState"]})'))
            print("ダイナミック  :", f'{data["dynamicRate"]} %')
            print("RSSI 品質     :", "不良" if data["rssiQualityBad"] else "正常")
        else:
            print("点灯モード    :", STRIP_MODE_NAMES.get(data["mode"], f'? ({data["mode"]})'))
            print("色 (各成分0〜3):", data["colors"] or "なし")
            print("フォールト    :", data["faultCode"] or "なし")
        print("ネットワーク  :", NETWORK_STATUS_NAMES.get(data["networkStatus"],
                                                          f'? ({data["networkStatus"]})'))
        print("シーケンス    :", data["sequence"],
              "(更新のたびに増える。動かない個体は値が古い可能性あり)")
        print("BLE rssi      :", data["rssi"])
        print("-----------", flush=True)

        # ここに取得データによるアクションを記述
        # if not data["isOn"]:
        #     ...


async def find_lights(seconds=10):
    """周囲のランプを列挙する。"""
    found = {}

    def cb(device, adv):
        data = parse_light(adv)
        if data:
            found[device.address.lower()] = (data, adv.rssi)

    async with BleakScanner(cb):
        await asyncio.sleep(seconds)

    print(f"--- {seconds}秒スキャン結果: ランプ {len(found)}台 ---")
    for addr, (d, rssi) in sorted(found.items()):
        name = DEVICE_TYPE_NAMES.get(d["deviceType"], "?")
        state = "点灯" if d["isOn"] else "消灯"
        print(f"  {addr}  0x{d['deviceType']:02x}  {name:28} {state}  明るさ{d['brightness']:3}%  rssi={rssi:4}")
    if not found:
        print("  見つかりませんでした。")
        print("  電源が入っているか、電波が届く範囲にあるかを確認してください。")
        print("  周囲の SwitchBot 機器の一覧は --devices で確認できます。")
    return sorted(found)


async def main():
    ap = argparse.ArgumentParser(description="SwitchBot スマート電球 / テープライト BLE スキャナ")
    ap.add_argument("--mac", action="append", default=[],
                    help="対象の MAC アドレス。複数台は --mac を繰り返す")
    ap.add_argument("--watch", action="store_true",
                    help="MAC を指定せず、受信できたランプをすべて監視する")
    ap.add_argument("--scan", action="store_true", help="周囲のランプを探して終了")
    ap.add_argument("--devices", action="store_true",
                    help="ランプに限らず周囲の SwitchBot 機器を一覧表示して終了")
    ap.add_argument("--all", action="store_true",
                    help="状態が変わらなくても毎回表示する (既定は変化時のみ)")
    ap.add_argument("--raw", action="store_true",
                    help="メーカー固有データを生のまま表示する (バイト配置の検証用)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.devices:
        await discover_switchbots(highlight=DEVICE_TYPE_BULB)
        return
    if args.scan:
        await find_lights()
        return
    if not args.mac and not args.watch:
        ap.error("--mac を指定するか、--watch で全台を監視、--scan で MAC を調べてください")

    if _STRIPPED:
        log.warning("システムの dist-packages を import パスから除外しました: %s",
                    ", ".join(_STRIPPED))

    target = ", ".join(m.lower() for m in args.mac) if args.mac else "受信できた全台"
    log.info("%s の受信を開始します (Ctrl-C で終了)", target)
    log.info("この機種は実機で未検証です。値がおかしければ --raw の出力を確認してください")
    if not args.all and not args.raw:
        log.info("状態が変わったときだけ表示します (毎回見るには --all)")

    light = Light(
        args.mac,
        on_update=None if args.raw else StatePrinter(show_all=args.all),
        on_raw=print_raw if args.raw else None,
    )
    await light.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
