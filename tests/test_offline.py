#!/usr/bin/env python3
# coding: utf-8
"""BLE ハードウェアなしで解析ロジックを検証する。

実機キャプチャをそのまま再生し、SwitchBot 公式仕様書
(OpenWonderLabs/SwitchBotAPI-BLE, devicetypes/contactsensor.md)
どおりに読めているかを確認する。

実行:
    ./venv/bin/python tests/test_offline.py
"""
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import switchbot_contact as sc

CONTACT = "0000000d-0000-1000-8000-00805f9b34fb"
FD3D = "0000fd3d-0000-1000-8000-00805f9b34fb"
OTHER = "0000fe9f-0000-1000-8000-00805f9b34fb"

# 実機 (c4:88:9c:aa:ab:2f) の 14:44 キャプチャ。ログから1バイトずつ転記。
CAPTURE = [
    ("14:44:02", "64 20 00 00 01 29 01 25 c1"),
    ("14:44:10", "64 20 00 00 01 35 01 31 c1"),
    ("14:44:12", "64 20 00 02 01 37 00 01 41"),   # ドアを開けた
    ("14:44:15", "64 20 00 00 01 3a 00 01 41"),   # 閉めた
    ("14:44:21", "64 60 00 00 00 05 00 06 42"),   # 近づいてボタンを押した
    ("14:44:25", "64 60 00 00 00 09 00 0a 42"),
]


def pkt(hx):
    return bytes.fromhex(hx.replace(" ", ""))


# ---------------------------------------------------------------- スキャン分類
class Dev:
    def __init__(self, a):
        self.address = a


class Adv:
    def __init__(self, sd, rssi, name=None, md=None):
        self.service_data, self.rssi, self.local_name = sd, rssi, name
        self.manufacturer_data = md or {}


FAKE = [
    (Dev("c4:88:9c:aa:ab:2f"), Adv({FD3D: pkt(CAPTURE[2][1])}, -61)),
    (Dev("dc:87:13:08:75:83"), Adv({CONTACT: bytes([0x63, 0, 80, 0, 0, 0, 0, 0, 0])}, -55)),
    (Dev("70:04:1d:7d:8e:12"), Adv({OTHER: b"\x6a\x01\x02"}, -40, "SomeBeacon")),
    (Dev("52:70:13:d5:f6:47"), Adv({}, -41)),
]


class FakeScanner:
    def __init__(self, cb, *a, **k):
        self.cb = cb

    async def __aenter__(self):
        for d, adv in FAKE:
            self.cb(d, adv)
        return self

    async def __aexit__(self, *a):
        return False


sc.BleakScanner = FakeScanner
_sleep = asyncio.sleep
asyncio.sleep = lambda s: _sleep(0)
asyncio.run(sc.discover_switchbots(10))

# ---------------------------------------------------------------- 解析
print()
print("=== 実機キャプチャの再生 ===")
DOOR = sc.DOOR_STATE_NAMES
print(f"{'時刻':>8} {'人感':>4} {'ドア':>10} {'照度':>4} {'PIR秒':>6} {'HAL秒':>6} {'入室':>4} {'ボタン':>5}")
rows = []
for ts, hx in CAPTURE:
    d = sc.parse_contact(pkt(hx))
    rows.append(d)
    print(f"{ts:>8} {d['isMotion']:>4} {DOOR[d['doorState']]:>10} {d['isIlluminance']:>4} "
          f"{d['secSincePir']:>6} {d['secSinceHal']:>6} {d['entranceCount']:>4} {d['buttonCount']:>5}")

# ドアを開けた瞬間: HAL がリセットされ、PIR は走り続ける
assert rows[2]["doorState"] == sc.DOOR_OPEN and rows[2]["isOpen"] == 1
assert rows[2]["secSinceHal"] == 1 and rows[2]["secSincePir"] == 311
# 入室カウンタは 1..3 で循環する (3 -> 1)
assert rows[1]["entranceCount"] == 3 and rows[2]["entranceCount"] == 1
# 近づいてボタンを押した: PIR が立ち、PIR 秒がリセットされ、ボタンカウンタが +1
assert rows[4]["isMotion"] == 1 and rows[4]["secSincePir"] == 5
assert rows[3]["buttonCount"] == 1 and rows[4]["buttonCount"] == 2
# 16bit を超える値が読める (255 で頭打ちにならない)
assert rows[0]["secSincePir"] == 297 and rows[0]["secSinceHal"] == 293
print("実機キャプチャと公式仕様が一致")

print()
print("=== ドア状態は 2bit の値 (独立フラグではない) ===")
for raw, want in ((0b000, sc.DOOR_CLOSED), (0b010, sc.DOOR_OPEN), (0b100, sc.DOOR_TIMEOUT)):
    d = sc.parse_contact(bytes([0x64, 0, 0, raw, 0, 0, 0, 0, 0]))
    print(f"  [3]=0b{raw:03b} -> {DOOR[d['doorState']]:10} isOpen={d['isOpen']} isLeaveOpen={d['isLeaveOpen']}")
    assert d["doorState"] == want
# 値 2 (開いたままタイムアウト) はドアが開いている状態
assert sc.parse_contact(bytes([0x64, 0, 0, 0b100, 0, 0, 0, 0, 0]))["isOpen"] == 1
print("開けっ放し (値2) を isOpen=1 として扱えている")

print()
print("=== 17bit カウンタ ([3] の最上位ビット) ===")
d = sc.parse_contact(bytes([0x64, 0, 0, 0b11000000, 0xFF, 0xFF, 0xFF, 0xFF, 0]))
print("  PIR:", d["secSincePir"], " HAL:", d["secSinceHal"])
assert d["secSincePir"] == 131071 and d["secSinceHal"] == 131071
print("65535 を超える値が読める")

print()
print("=== ボタン押下検出 (1..15 循環) ===")
s = sc.ContactSensor("aa:bb")
seq = [3, 3, 4, 4, 15, 1, 1, 2]
want = [0, 0, 1, 0, 1, 1, 0, 1]
got = [s._detect_press(c) for c in seq]
print("count :", seq)
print("実際   :", got)
assert got == want, got
print("押下検出 OK (15->1 の折り返しも検出)")

print()
print("すべて OK")
