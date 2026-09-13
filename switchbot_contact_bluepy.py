#!/usr/bin/env python3
# coding: utf-8
"""
SwitchBot 開閉センサー BLE スキャナ (bluepy 版 / 元コードの修正版)

元コードからの変更点:
  - MAC アドレスを小文字に正規化 (bluepy の dev.addr は必ず小文字)
  - 初回スキャンで見つからなくても落ちないよう output を dict で初期化
  - Scanner インスタンスを使い回す
  - バッテリー残量を追加取得
  - 未使用引数 startScan(self, scan, time) の scan を削除
実行には root 権限 (sudo) か bluepy-helper への setcap が必要です。
"""
import time as _time

# bluepy を import する前に、システムの dist-packages を排除する (sitefix.py 参照)
from sitefix import strip_dist_packages

strip_dist_packages()

from bluepy.btle import Scanner, DefaultDelegate, BTLEException  # noqa: E402

DEVICE_TYPE_CONTACT = 0x64  # 開閉センサー = 'd'


class ScanDelegate(DefaultDelegate):
    def __init__(self, macaddr):
        DefaultDelegate.__init__(self)
        self.macaddr = macaddr.lower()          # bluepy は小文字で返す
        self.prev_button_count = None           # 未観測を None で表す
        self.output = {                         # dict で初期化 (元コードは list でバグ)
            "found": False,
            "isIlluminance": 0, "isOpen": 0, "isLeaveOpen": 0,
            "time": 0, "buttonCount": 0, "isButton": 0, "battery": 0,
        }
        self._scanner = Scanner().withDelegate(self)

    def handleDiscovery(self, dev, isNewDev, isNewData):
        if dev.addr.lower() != self.macaddr:
            return

        for (adtype, desc, value) in dev.getScanData():
            if adtype != 22:                    # 22 = 0x16 = Service Data (16bit UUID)
                continue
            try:
                servicedata = bytes.fromhex(value[4:])   # 先頭2バイトの UUID を捨てる
            except ValueError:
                continue
            if len(servicedata) < 9:
                continue
            if (servicedata[0] & 0x7F) != DEVICE_TYPE_CONTACT:
                continue                        # 開閉センサー以外のパケットは無視

            battery       = servicedata[2] & 0b01111111        # バッテリー残量 %
            isIlluminance =  servicedata[3] & 0b00000001       # 明るい=1 / 暗い=0
            isOpen        = (servicedata[3] & 0b00000010) >> 1  # 開=1 / 閉=0
            isLeaveOpen   = (servicedata[3] & 0b00000100) >> 2  # 開けっ放し=1
            open_seconds  =  servicedata[7]                     # 開けっ放し経過秒
            buttonCount   =  servicedata[8] & 0b00001111        # 1..15 で循環

            isButton = self._detect_press(buttonCount)

            self.output = {
                "found": True,
                "isIlluminance": isIlluminance,
                "isOpen": isOpen,
                "isLeaveOpen": isLeaveOpen,
                "time": open_seconds,
                "buttonCount": buttonCount,
                "isButton": isButton,
                "battery": battery,
            }

    def _detect_press(self, count):
        """循環カウンタ (1..15) の差分でボタン押下を検出する。"""
        if self.prev_button_count is None:
            self.prev_button_count = count      # 初回は基準を取るだけ
            return 0
        delta = (count - self.prev_button_count) % 15
        self.prev_button_count = count
        return 1 if delta else 0

    def startScan(self, duration):
        self.output["isButton"] = 0             # 押下は1スキャン分だけ立てる
        self._scanner.scan(duration, passive=True)
        return self.output


if __name__ == '__main__':
    MACADDR = 'c4:88:9c:aa:ab:2f'   # ← あなたのデバイスの MAC に書き換えてください (小文字)
    SCAN_SECONDS = 5

    scan = ScanDelegate(MACADDR)

    while True:
        try:
            out = scan.startScan(SCAN_SECONDS)
        except BTLEException as e:
            print("BLE error:", e, "-- 3秒後に再試行します")
            _time.sleep(3)
            continue

        print("-----------")
        if not out["found"]:
            print("device not found (MACアドレス / 電池 / 距離を確認してください)")
            print("-----------")
            continue

        print("isIlluminance:", out["isIlluminance"])  # 明るい=1 暗い=0
        print("isOpen:",        out["isOpen"])         # 開=1 閉=0
        print("isLeaveOpen:",   out["isLeaveOpen"])    # 開けっ放し=1
        print("time:",          out["time"])           # 開けっ放し時間(秒)
        print("buttonCount:",   out["buttonCount"])    # 1..15 循環
        print("isButton:",      out["isButton"])       # 押された=1
        print("battery:",       out["battery"], "%")
        print("-----------")

        # ここに取得データによるアクションを記述
        # if out["isButton"]:
        #     ...
