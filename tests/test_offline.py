"""BLE ハードウェアなしで解析ロジックを検証する。

実行:
    ./venv/bin/python tests/test_offline.py
"""
import asyncio
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import switchbot_contact as sc

CONTACT = "0000000d-0000-1000-8000-00805f9b34fb"
FD3D    = "0000fd3d-0000-1000-8000-00805f9b34fb"
OTHER   = "0000fe9f-0000-1000-8000-00805f9b34fb"

class Dev:
    def __init__(self, a): self.address = a
class Adv:
    def __init__(self, sd, rssi, name=None):
        self.service_data, self.rssi, self.local_name = sd, rssi, name

# 実機の値を模したパケット: 明るい/開/開けっ放しでない, 12秒, ボタン3回, 電池92%
contact_payload = bytes([0x64, 0x00, 92, 0b00000011, 0, 0, 0, 12, 0x03])
FAKE = [
    (Dev("c4:88:9c:aa:ab:2f"), Adv({CONTACT: contact_payload}, -61)),
    (Dev("dc:87:13:08:75:83"), Adv({FD3D: bytes([0x63,0,80,0,0,0,0,0,0])}, -55)),
    (Dev("70:04:1d:7d:8e:12"), Adv({OTHER: b"\x6a\x01\x02"}, -40, "SomeBeacon")),
    (Dev("52:70:13:d5:f6:47"), Adv({}, -41)),
]

class FakeScanner:
    def __init__(self, cb, *a, **k): self.cb = cb
    async def __aenter__(self):
        for d, adv in FAKE: self.cb(d, adv)
        return self
    async def __aexit__(self, *a): return False

sc.BleakScanner = FakeScanner
orig_sleep = asyncio.sleep
async def no_sleep(s): await orig_sleep(0)
asyncio.sleep = no_sleep

asyncio.run(sc.discover_switchbots(10))

print()
print("=== 解析ロジックの検証 ===")
d = sc.parse_contact(contact_payload)
print(d)
assert d == {"battery":92,"isIlluminance":1,"isOpen":1,"isLeaveOpen":0,"time":12,"buttonCount":3}, d
print("parse_contact OK")

print()
print("=== ボタン押下検出 (1..15 循環) ===")
s = sc.ContactSensor("c4:88:9c:aa:ab:2f")
seq  = [3, 3, 4, 4, 15, 1, 1, 2]
want = [0, 0, 1, 0,  1, 1, 0, 1]   # 初回は基準取りなので 0
got = [s._detect_press(c) for c in seq]
print("count :", seq)
print("期待   :", want)
print("実際   :", got)
assert got == want, got
print("押下検出 OK (15->1 の折り返しも検出)")

print()
print("すべて OK")
