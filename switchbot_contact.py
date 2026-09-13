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
import time
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
    """SwitchBot 開閉センサーのサービスデータを解析する。対象外なら None。

    バイト配置は実機 (W1201500, サービス UUID fd3d) の観測から導いたもので、
    公式仕様書ではありません。`--raw` で自分の個体を確認できます。

        [0]      デバイス種別 (0x64 = 'd')
        [1]      フラグ。ボタン押下で 0x20 -> 0x60 と変化する。用途未特定
        [2]      常に 0 (観測範囲では)
        [3] bit0 照度      1=明るい 0=暗い   ※未検証
            bit1 開閉状態  1=開     0=閉
            bit2 開けっ放し
        [4][5]   最後のボタン押下からの経過秒 (16bit ビッグエンディアン)
        [6][7]   最後の開閉からの経過秒       (16bit ビッグエンディアン)
        [8] bit7 ドアを開けるとクリアされる。用途未特定
            下位4bit ボタン押下回数 (1..15 で循環)

    バッテリー残量はサービスデータに含まれていません。[1] [2] はいずれも
    バッテリーではないことを確認済みです (ボタン押下で [1] が 0x20 -> 0x60 と
    跳ねるため)。メーカー固有データ側にある可能性が高く、調査中です。
    """
    if len(payload) < 9 or (payload[0] & 0x7F) != DEVICE_TYPE_CONTACT:
        return None
    return {
        "isIlluminance":  payload[3] & 0b00000001,         # 明るい=1 / 暗い=0 ※未検証
        "isOpen":        (payload[3] & 0b00000010) >> 1,   # 開=1 / 閉=0
        "isLeaveOpen":   (payload[3] & 0b00000100) >> 2,   # 開けっ放し=1
        "secSinceButton": (payload[4] << 8) | payload[5],  # 最後のボタン押下からの秒数
        "secSinceChange": (payload[6] << 8) | payload[7],  # 最後の開閉からの秒数
        "time":           (payload[6] << 8) | payload[7],  # 旧名。secSinceChange と同じ
        "buttonCount":    payload[8] & 0b00001111,         # 1..15 で循環
        "battery":        None,                            # サービスデータには無い
    }


class ContactSensor:
    def __init__(self, macaddr, on_update=None, on_raw=None):
        self.macaddr = macaddr.lower()
        self.on_update = on_update
        self.on_raw = on_raw
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
        if self.on_raw:
            self.on_raw(adv)

        for uuid, payload in adv.service_data.items():
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


def _u16(b, i):
    """ビッグエンディアン16bitとして読む。範囲外なら None。"""
    return (b[i] << 8) | b[i + 1] if len(b) > i + 1 else None


def _dump_bytes(payload, indent="          "):
    for i in range(0, len(payload), 5):
        cells = [f"[{j}]={payload[j]:3d}/{payload[j]:08b}" for j in range(i, min(i + 5, len(payload)))]
        print(indent, "  ".join(cells))


def print_raw(adv):
    """サービスデータとメーカー固有データを生のまま表示する。バイト配置の検証用。"""
    ts = time.strftime("%H:%M:%S")
    print(f"=== {ts}  rssi={adv.rssi}")

    for uuid, payload in sorted(adv.service_data.items()):
        print(f"  service_data uuid={uuid.lower()[4:8]} len={len(payload):2}  "
              + " ".join(f"{b:02x}" for b in payload))
        _dump_bytes(payload)
        if len(payload) >= 9:
            # 16bit カウンタ候補と [8] のニブル分解
            print(f"           u16[4:5]={_u16(payload, 4):5}   u16[6:7]={_u16(payload, 6):5}"
                  f"   [8]hi={payload[8] >> 4:2} [8]lo={payload[8] & 0x0F:2}")

    for cid, payload in sorted(adv.manufacturer_data.items()):
        vendor = " (SwitchBot)" if cid == 0x0969 else ""
        print(f"  manufacturer_data id=0x{cid:04x}{vendor} len={len(payload):2}  "
              + " ".join(f"{b:02x}" for b in payload))
        _dump_bytes(payload)

    if not adv.service_data and not adv.manufacturer_data:
        print("  (データなし)")
    print(flush=True)


# 状態とみなすキー。time と rssi は毎秒動くので差分判定から除く。
STATE_KEYS = ("isIlluminance", "isOpen", "isLeaveOpen", "buttonCount")


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
        print("isIlluminance:", data["isIlluminance"])  # 明るい=1 暗い=0
        print("isOpen:",        data["isOpen"])         # 開=1 閉=0
        print("isLeaveOpen:",   data["isLeaveOpen"])    # 開けっ放し=1
        print("secSinceChange:", data["secSinceChange"], "秒")  # 最後の開閉から
        print("secSinceButton:", data["secSinceButton"], "秒")  # 最後のボタン押下から
        print("buttonCount:",   data["buttonCount"])    # 1..15 循環
        print("isButton:",      data["isButton"])       # 押された=1
        print("battery:",       "不明 (サービスデータに無し)"
              if data["battery"] is None else f"{data['battery']} %")
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
    ap.add_argument("--all", action="store_true",
                    help="状態が変わらなくても毎回表示する (既定は変化時のみ)")
    ap.add_argument("--raw", action="store_true",
                    help="サービスデータを生のまま表示する (バイト配置の検証用)")
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
    if not args.all and not args.raw:
        log.info("状態が変わったときだけ表示します (毎回見るには --all)")

    sensor = ContactSensor(
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
