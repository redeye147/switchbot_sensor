#!/usr/bin/env python3
# coding: utf-8
"""
SwitchBot 開閉センサー BLE スキャナ (bleak 版 / ラズパイ常駐向け・推奨)

bluepy は 2019 年以降メンテされておらず、新しい Raspberry Pi OS (Bookworm 以降 /
Python 3.11+) ではビルドに失敗することがあります。bleak は BlueZ の D-Bus API を
使うため root 権限が不要で、systemd で一般ユーザ常駐させやすいのが利点です。

  pip install bleak
  python3 switchbot_contact.py --mac c4:88:9c:aa:ab:2f
  python3 switchbot_contact.py --scan      # 周囲の SwitchBot を探して MAC を調べる
"""
import argparse
import asyncio
import logging
import unicodedata

# bleak を import する前に、システムの dist-packages を排除する。
# PYTHONPATH に /usr/lib/python3/dist-packages が入っていると venv より優先され、
# 古い typing_extensions を掴んで bleak が ImportError になるため。
from sitefix import strip_dist_packages

_STRIPPED = strip_dist_packages()

from bleak import BleakScanner  # noqa: E402

DEVICE_TYPE_CONTACT = 0x64  # 開閉センサー = 'd'

# SwitchBot がサービスデータに使う 16bit UUID (0xFD3D と旧来の 0x000D)
SWITCHBOT_SERVICE_UUIDS = {
    "0000fd3d-0000-1000-8000-00805f9b34fb",
    "0000000d-0000-1000-8000-00805f9b34fb",
}

# サービスデータ先頭バイトの既知の種別。網羅ではないので未知は "?" と表示する。
DEVICE_TYPE_NAMES = {
    0x48: "Bot",
    0x54: "温湿度計",
    0x63: "カーテン",
    0x64: "開閉センサー",
    0x73: "人感センサー",
    0x75: "リモートボタン",
    0x77: "温湿度計 Plus",
}

log = logging.getLogger("switchbot")


def parse_contact(payload: bytes):
    """SwitchBot 開閉センサーのサービスデータを解析する。対象外なら None。"""
    if len(payload) < 9 or (payload[0] & 0x7F) != DEVICE_TYPE_CONTACT:
        return None
    return {
        "battery":       payload[2] & 0b01111111,          # バッテリー残量 %
        "isIlluminance":  payload[3] & 0b00000001,         # 明るい=1 / 暗い=0
        "isOpen":        (payload[3] & 0b00000010) >> 1,   # 開=1 / 閉=0
        "isLeaveOpen":   (payload[3] & 0b00000100) >> 2,   # 開けっ放し=1
        "time":           payload[7],                      # 開けっ放し経過秒
        "buttonCount":    payload[8] & 0b00001111,         # 1..15 で循環
    }


class ContactSensor:
    def __init__(self, macaddr, on_update=None):
        self.macaddr = macaddr.lower()
        self.on_update = on_update
        self.prev_button_count = None
        self.state = None

    def _detect_press(self, count):
        """循環カウンタ (1..15) の差分でボタン押下を検出する。"""
        if self.prev_button_count is None:
            self.prev_button_count = count      # 初回は基準を取るだけ
            return 0
        delta = (count - self.prev_button_count) % 15
        self.prev_button_count = count
        return 1 if delta else 0

    def _handle(self, device, adv):
        if device.address.lower() != self.macaddr:
            return
        for payload in adv.service_data.values():
            data = parse_contact(payload)
            if data is None:
                continue
            data["isButton"] = self._detect_press(data["buttonCount"])
            data["rssi"] = adv.rssi
            self.state = data
            if self.on_update:
                self.on_update(data)
            return

    async def run(self):
        """BLE アドバタイズを受信し続ける (接続はしない)。"""
        while True:
            try:
                async with BleakScanner(self._handle):
                    await asyncio.Future()      # 永久に受信し続ける
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("BLE スキャンが停止しました (%s) -- 5秒後に再開します", e)
                await asyncio.sleep(5)


def _pad(text, width):
    """全角を2桁として数え、表示幅を揃える。"""
    w = sum(2 if unicodedata.east_asian_width(c) in "FWA" else 1 for c in text)
    return text + " " * max(0, width - w)


async def discover_switchbots(seconds=10):
    """周囲のデバイスを列挙する。SwitchBot はサービスデータの UUID で判別する。"""
    switchbots = {}
    others = {}

    def cb(device, adv):
        addr = device.address.lower()
        for uuid, payload in adv.service_data.items():
            if uuid.lower() in SWITCHBOT_SERVICE_UUIDS and payload:
                switchbots[addr] = (payload[0] & 0x7F, uuid.lower(), adv.rssi)
                return
        uuids = sorted(u.lower()[4:8] for u in adv.service_data)
        others[addr] = (adv.local_name, adv.rssi, uuids)

    async with BleakScanner(cb):
        await asyncio.sleep(seconds)

    print(f"--- {seconds}秒スキャン結果 ---")
    print()
    print(f"SwitchBot デバイス ({len(switchbots)}件)")
    if switchbots:
        for addr, (dtype, uuid, rssi) in sorted(switchbots.items()):
            name = DEVICE_TYPE_NAMES.get(dtype, "?")
            char = chr(dtype) if 32 <= dtype < 127 else "?"
            mark = "   <= これを --mac に指定" if dtype == DEVICE_TYPE_CONTACT else ""
            print(f"  {addr}  0x{dtype:02x} ({char}) {_pad(name, 14)} rssi={rssi:4}  uuid={uuid[4:8]}{mark}")
    else:
        print("  見つかりませんでした。センサーを近づける / 電池を確認してください。")

    print()
    print(f"その他の BLE デバイス ({len(others)}件) -- SwitchBot ではありません")
    for addr, (name, rssi, uuids) in sorted(others.items()):
        tag = ("uuid=" + ",".join(uuids)) if uuids else "service data なし"
        print(f"  {addr}  rssi={rssi:4}  {tag}  {name or ''}")


def print_state(data):
    print("-----------")
    print("isIlluminance:", data["isIlluminance"])  # 明るい=1 暗い=0
    print("isOpen:",        data["isOpen"])         # 開=1 閉=0
    print("isLeaveOpen:",   data["isLeaveOpen"])    # 開けっ放し=1
    print("time:",          data["time"])           # 開けっ放し時間(秒)
    print("buttonCount:",   data["buttonCount"])    # 1..15 循環
    print("isButton:",      data["isButton"])       # 押された=1
    print("battery:",       data["battery"], "%")
    print("rssi:",          data["rssi"])
    print("-----------", flush=True)

    # ここに取得データによるアクションを記述
    # if data["isButton"]:
    #     ...
    # if data["isLeaveOpen"]:
    #     ...


async def main():
    ap = argparse.ArgumentParser(description="SwitchBot 開閉センサー BLE スキャナ")
    ap.add_argument("--mac", help="対象デバイスの MAC アドレス (例 c4:88:9c:aa:ab:2f)")
    ap.add_argument("--scan", action="store_true", help="周囲の SwitchBot を探して MAC を表示して終了")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.scan:
        await discover_switchbots()
        return
    if not args.mac:
        ap.error("--mac を指定するか、--scan で MAC アドレスを調べてください")

    if _STRIPPED:
        log.warning("システムの dist-packages を import パスから除外しました: %s",
                    ", ".join(_STRIPPED))
        log.warning("PYTHONPATH の設定を見直すことをおすすめします (README 参照)")

    log.info("%s の受信を開始します (Ctrl-C で終了)", args.mac.lower())
    await ContactSensor(args.mac, on_update=print_state).run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
