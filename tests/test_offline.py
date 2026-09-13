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

# 実機 (14:44:12, ドアを開けた瞬間) のパケットそのもの
contact_payload = bytes.fromhex("642000020137000141")
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
assert d == {
    "isIlluminance": 0, "isOpen": 1, "isLeaveOpen": 0,
    "secSinceButton": 0x0137, "secSinceChange": 1, "time": 1,
    "buttonCount": 1, "battery": None,
}, d
print("parse_contact OK")

print()
print("=== 実機ログ全体を再生して整合性を確認 ===")
# (時刻, 生バイト列) -- 14:44 のキャプチャそのまま
CAPTURE = [
    ("14:44:02", "642000000129012 5c1"), ("14:44:10", "6420000001350131c1"),
    ("14:44:12", "642000020137000141"), ("14:44:15", "64200000013a000141"),
    ("14:44:21", "642060000005000642"), ("14:44:25", "64206000000900 0a42"),
]
prev_change = None
for ts, hx in CAPTURE:
    d = sc.parse_contact(bytes.fromhex(hx.replace(" ", "")))
    print(f"  {ts}  open={d['isOpen']}  開閉から={d['secSinceChange']:4}秒  "
          f"ボタンから={d['secSinceButton']:4}秒  count={d['buttonCount']}")
# 16bit として読めているか (255 を超える値が出ること)
assert sc.parse_contact(bytes.fromhex("6420000001290125c1"))["secSinceButton"] == 297
assert sc.parse_contact(bytes.fromhex("6420000001290125c1"))["secSinceChange"] == 293
print("16bit ビッグエンディアンとして読めている (297 / 293)")

# ドア開でのみ secSinceChange がリセットされ、ボタンでのみ secSinceButton がリセットされる
opened = sc.parse_contact(bytes.fromhex("642000020137000141"))
assert opened["secSinceChange"] == 1 and opened["secSinceButton"] == 311
pressed = sc.parse_contact(bytes.fromhex("642060000005000642"))
assert pressed["secSinceButton"] == 5 and pressed["buttonCount"] == 2
print("開閉リセットとボタンリセットが独立していることを確認")

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
