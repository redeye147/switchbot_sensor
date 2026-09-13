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

from switchbot_protocol import DOOR_STATE_NAMES, parse_contact

class ScanDelegate(DefaultDelegate):
    def __init__(self, macaddr):
        DefaultDelegate.__init__(self)
        self.macaddr = macaddr.lower()          # bluepy は小文字で返す
        self.prev_button_count = None           # 未観測を None で表す
        self.output = {"found": False}          # dict で初期化 (元コードは list でバグ)
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

            data = parse_contact(servicedata)
            if data is None:
                continue                        # 開閉センサー以外のパケットは無視

            data["isButton"] = self._detect_press(data["buttonCount"])
            data["found"] = True
            self.output = data
            return

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

        print("ドア          :", DOOR_STATE_NAMES[out["doorState"]])
        print("照度          :", "明るい" if out["isIlluminance"] else "暗い")
        print("人感 (PIR)    :", "動きあり" if out["isMotion"] else "動きなし")
        print("最後の開閉から:", out["secSinceHal"], "秒")
        print("最後の人感から:", out["secSincePir"], "秒")
        print("ボタン        :", f'count={out["buttonCount"]} 押された={out["isButton"]}')
        print("バッテリー    :", out["battery"], "%")
        print("-----------")

        # ここに取得データによるアクションを記述
        # if out["isButton"]:
        #     ...
