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
import switchbot_protocol as sc_proto

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
print("=== 人感センサー (motionsensor.md) ===")
# [0]='s', [1] PIR検知中, [2] 電池90%, [3][4]=0x0102, [5]=LED有/IoT有/中距離/明るい
m = sc_proto.parse_motion(bytes([0x73, 0b01000000, 90, 0x01, 0x02, 0b00110110]))
print("  ", m)
assert m["isMotion"] == 1 and m["battery"] == 90 and m["secSincePir"] == 258
assert m["ledEnabled"] == 1 and m["iotEnabled"] == 1
assert m["sensingDistance"] == 1 and m["isIlluminance"] == 1

# 経過秒は 17bit: [5] bit7 が最上位。[4] だけ読むと 255 で一周してしまう。
m = sc_proto.parse_motion(bytes([0x73, 0, 0, 0xFF, 0xFF, 0b10000000]))
print("   17bit 最大値:", m["secSincePir"])
assert m["secSincePir"] == 131071
m = sc_proto.parse_motion(bytes([0x73, 0, 0, 0x01, 0x00, 0b00000001]))
print("   [4]=0 でも 256 秒と読める:", m["secSincePir"])
assert m["secSincePir"] == 256, m["secSincePir"]

# 明るさは 01=暗い / 10=明るい。00 と 11 は予約値なので None を返す。
for bits, want in ((0b01, 0), (0b10, 1), (0b00, None), (0b11, None)):
    got = sc_proto.parse_motion(bytes([0x73, 0, 0, 0, 0, bits]))["isIlluminance"]
    print(f"   [5]&0b11=0b{bits:02b} -> isIlluminance={got}")
    assert got == want

# 開閉センサーのパケットを人感として読まない (種別バイトで弾く)
assert sc_proto.parse_motion(pkt(CAPTURE[0][1])) is None
assert sc_proto.parse_contact(bytes([0x73, 0, 0, 0, 0, 0, 0, 0, 0])) is None
print("人感センサー OK (種別バイトで取り違えない)")

print()
print("=== カーテン (curtain.md) ===")
# [0]='c', [1] 接続可+較正済, [2] 電池77%, [3] 動作中+位置30%, [4] 明るさ5/チェーン1
c = sc_proto.parse_curtain(bytes([0x63, 0b11000000, 77, 0b10011110, 0x51]))
print("  ", c)
assert c["connectable"] == 1 and c["calibrated"] == 1 and c["battery"] == 77
assert c["isMoving"] == 1 and c["position"] == 30
assert c["lightLevel"] == 5 and c["deviceChain"] == 1

# 動作ビットは位置に混ざらない (位置は下位7bit)
stopped = sc_proto.parse_curtain(bytes([0x63, 0, 0, 100, 0]))
moving = sc_proto.parse_curtain(bytes([0x63, 0, 0, 100 | 0x80, 0]))
print(f"   停止 position={stopped['position']} isMoving={stopped['isMoving']}")
print(f"   動作 position={moving['position']} isMoving={moving['isMoving']}")
assert stopped["position"] == moving["position"] == 100
assert stopped["isMoving"] == 0 and moving["isMoving"] == 1

# 未較正
assert sc_proto.parse_curtain(bytes([0x63, 0b10000000, 0, 0, 0]))["calibrated"] == 0

# Curtain 3 ('[' 0x5B) は別形式なので解析しない
assert sc_proto.parse_curtain(bytes([0x5B, 0, 0, 0, 0])) is None
# 他機種のパケットを取り違えない
assert sc_proto.parse_curtain(pkt(CAPTURE[0][1])) is None
assert sc_proto.parse_contact(bytes([0x63, 0, 0, 0, 0, 0, 0, 0, 0])) is None
assert sc_proto.parse_motion(bytes([0x63, 0, 0, 0, 0, 0])) is None
print("カーテン OK (Curtain 3 と他機種は弾く)")

print()
print("=== プラグミニ (plugmini.md) ===")
# MAC + seq=5 + ON + タイマー/UTC同期 + wifi rssi -58 + 消費電力 456 (45.6W)
md = bytes([0xd8, 0x3b, 0xda, 0x25, 0xd9, 0xe6, 5, 0x80, 0b110, 256 - 58, 456 >> 8, 456 & 0xFF])
pl = sc_proto.parse_plug(md)
print("  ", pl)
assert pl["mac"] == "d8:3b:da:25:d9:e6" and pl["sequence"] == 5
assert pl["isOn"] == 1 and pl["hasTimer"] == 1 and pl["hasDelay"] == 0 and pl["utcSynced"] == 1
assert pl["powerRaw"] == 456 and pl["powerW"] == 45.6
assert pl["isOverload"] == 0

# バイトが負の RSSI を表していれば dBm として解釈する
assert pl["wifiRssiRaw"] == 198 and pl["wifiRssiDbm"] == -58

# OFF と過負荷。過負荷ビットは消費電力に混ざらない (電力は 15bit)
off = sc_proto.parse_plug(bytes([0] * 6 + [1, 0x00, 0, 0, 0x80 | 0x7F, 0xFF]))
print(f"   OFF: isOn={off['isOn']} 過負荷={off['isOverload']} 電力生値={off['powerRaw']}")
assert off["isOn"] == 0 and off["isOverload"] == 1 and off["powerRaw"] == 0x7FFF

# 短いペイロードは弾く
assert sc_proto.parse_plug(b"\x01\x02") is None

# 実機 (種別 0x6A, ON, 66.7W) のメーカー固有データ
real = bytes.fromhex("70041d7d8e12408012300 29b".replace(" ", ""))
r = sc_proto.parse_plug(real)
print(f"   実機: isOn={r['isOn']} {r['powerW']}W seq={r['sequence']} wifi生値={r['wifiRssiRaw']}")
assert r["isOn"] == 1 and r["powerW"] == 66.7 and r["mac"] == "70:04:1d:7d:8e:12"
# [9] は仕様書では RSSI だが実機は正の値を返す。dBm とは解釈しない。
assert r["wifiRssiRaw"] == 48 and r["wifiRssiDbm"] is None

# 機種の判別はサービスデータ側で行う。メーカー固有データは全機種が同じ
# company ID で出し、先頭6バイトも一様に MAC なので判別材料にならない。
# (開閉センサーが「プラグ・1302W」と誤検出された原因)
FD3D_U = "0000FD3D-0000-1000-8000-00805F9B34FB"   # 大文字でも拾えること
assert sc_proto.service_device_type({FD3D_U: bytes([0x67, 0, 0])}) == 0x67
assert sc_proto.service_device_type({FD3D: pkt(CAPTURE[0][1])}) == 0x64
assert sc_proto.service_device_type({OTHER: b"\x67\x00"}) is None
assert sc_proto.service_device_type({}) is None
print("   機種の判別:", sc_proto.DEVICE_TYPE_NAMES[0x67], "/", sc_proto.DEVICE_TYPE_NAMES[0x64])
# 実機のサービスデータは 3 バイトしかない。開閉センサーとして読もうとしないこと。
plug_sd = bytes.fromhex("6a0064")
assert sc_proto.service_device_type({FD3D: plug_sd}) == 0x6A
assert sc_proto.parse_contact(plug_sd) is None
assert sc_proto.parse_motion(plug_sd) is None
assert sc_proto.parse_curtain(plug_sd) is None
print("プラグミニ OK (機種バイトで誤検出を防ぐ)")

print()
print("=== スマート電球 / テープライト ===")
MAC6 = bytes([0x11, 0x22, 0x33, 0x44, 0x55, 0x66])
# 電球: 点灯 明るさ80% / IoT接続済み + プリセット + カラー / ダイナミック50% / ループ4
b = sc_proto.parse_bulb(MAC6 + bytes([7, 0x80 | 80, (2 << 4) | 0b1000 | 2, 50, 0b000100_00]))
print("   電球:", b)
assert b["isOn"] == 1 and b["brightness"] == 80 and b["networkStatus"] == 2
assert b["isPreset"] == 1 and b["lightState"] == 2 and b["dynamicRate"] == 50
assert b["rssiQualityBad"] == 0 and b["loopIndex"] == 4

# 消灯かつ明るさ 100%。電源ビットが明るさに混ざらないこと
off = sc_proto.parse_bulb(MAC6 + bytes([1, 100, 0, 0, 0]))
assert off["isOn"] == 0 and off["brightness"] == 100

# テープライト: 色データは 2bit x 24。R:G:B=0:0:0 の色は「無し」として除く
strip = sc_proto.parse_strip(
    MAC6 + bytes([7, 0x80 | 60, (2 << 4) | 3]) + bytes([0b11_01_00_10, 0, 0, 0, 0, 0]) + bytes([0]))
print("   テープ:", strip)
assert strip["mode"] == 3 and strip["brightness"] == 60
assert strip["colors"] == [(3, 1, 0), (2, 0, 0)], strip["colors"]
assert strip["faultCode"] == 0

# 全ビット 1 なら 8 色すべてが (3,3,3)
full = sc_proto.parse_strip(MAC6 + bytes([1, 0, 0]) + b"\xff" * 6 + bytes([0]))
assert full["colors"] == [(3, 3, 3)] * 8, full["colors"]
# 全ビット 0 なら色は 1 つも無い
none = sc_proto.parse_strip(MAC6 + bytes([1, 0, 0]) + b"\x00" * 6 + bytes([0]))
assert none["colors"] == []

# 長さが足りないものは弾く (テープライトは電球より長いデータが要る)
assert sc_proto.parse_bulb(MAC6 + bytes([0, 0, 0])) is None
assert sc_proto.parse_strip(MAC6 + bytes([7, 0, 0, 0, 0])) is None
print("   機種名:", sc_proto.DEVICE_TYPE_NAMES[0x75], "/", sc_proto.DEVICE_TYPE_NAMES[0x72])
print("ランプ OK ※実機未検証")

print()
print("=== 電球の制御コマンド (colorbulb.md) ===")
import switchbot_control as ctl

# 仕様書の Example と 1 バイトも違わないこと
SPEC_EXAMPLES = [
    ("Turn On Bulb",              ctl.build_command("on"),                      "570f470101"),
    ("Turn Off Bulb",             ctl.build_command("off"),                     "570f470102"),
    ("blue 50% brightness",       ctl.build_command("rgb", level=50, rgb=(0, 0, 255)),
                                                                                "570f470112320000ff"),
    ("read status",               ctl.build_command("status"),                  "570f4801"),
]
for name, got, want in SPEC_EXAMPLES:
    print(f"   {name:22} {got.hex()}  (仕様書: {want})")
    assert got.hex() == want, (name, got.hex(), want)

# 仕様書の Example にある応答を読めること
on_resp = bytes.fromhex("018032FF00000000FFFF02")
blue_resp = bytes.fromhex("0180320000FF0000FFFF02")
off_resp = bytes.fromhex("010032FF00000000FFFF02")
print()
print(ctl.describe_response(blue_resp))
assert "OK" in ctl.describe_response(on_resp)
assert "点灯" in ctl.describe_response(on_resp)
assert "消灯" in ctl.describe_response(off_resp)
assert "RGB       : 0, 0, 255" in ctl.describe_response(blue_resp)
assert "明るさ    : 50 %" in ctl.describe_response(blue_resp)

# エラーステータスは素直に伝える
assert "デバイスがビジー" in ctl.describe_response(bytes([0x03]))
assert "パスワード誤り" in ctl.describe_response(bytes([0x09]))
assert "応答なし" in ctl.describe_response(b"")
print()
print("電球の制御コマンド OK (仕様書の Example と一致)")

print()
print("=== バッテリーの注記 ===")
for b, want_warn in ((0, False), (19, True), (20, True), (21, False), (90, False)):
    note = sc_proto.battery_note(b)
    print(f"  {b:3}% -> {note or '(注記なし)'}")
    assert ("残り少なく" in note) == want_warn
# 0 は「残量 0%」ではなく未送信として扱う (毎回警告が鳴るのを避ける)
assert "残り少なく" not in sc_proto.battery_note(0)
print("低残量の警告 OK (0 は未送信として扱う)")

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
