"""BLE ハードウェアもスピーカーも無しで bath_timer のロジックを検証する。

実行:
    ./venv/bin/python tests/test_bath_timer.py
"""
import asyncio
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import bath_timer as bt, switchbot_protocol as proto

FD3D = "0000fd3d-0000-1000-8000-00805f9b34fb"
class Dev:
    def __init__(self, a): self.address = a
class Adv:
    def __init__(self, sd, md=None):
        self.service_data, self.manufacturer_data = sd, md or {}

def contact(count, open_=0):
    b = bytearray(bytes.fromhex("642000000129012 5c1".replace(" ","")))
    b[3] = open_ << 1
    b[8] = (b[8] & 0xF0) | count
    return Adv({FD3D: bytes(b)})

print("=== 開閉センサー: 押下カウンタで判定 ===")
d = bt.PressDetector(cooldown=0)
seq = [(contact(1), False),   # 初回は基準
       (contact(1), False),   # 変化なし
       (contact(2), True),    # 押された
       (contact(2), False),   # 同じパケットの再送
       (contact(3), True)]    # もう一度
for adv, want in seq:
    got = d(adv)
    print(f"   count={adv.service_data[FD3D][8] & 0x0F} -> {'押下' if got else '-'}")
    assert got == want

print()
print("=== ドアの開閉では反応しない (ボタンだけを見る) ===")
d = bt.PressDetector(cooldown=0)
d(contact(1)); 
assert d(contact(1, open_=1)) is False
assert d(contact(1, open_=0)) is False
print("   開閉しても押下扱いにならない")

print()
print("=== 未知の機種: ペイロードの変化で判定 ===")
def unknown(n):
    return Adv({FD3D: bytes([0x62, 0x00, n])})
d = bt.PressDetector(cooldown=0)
assert d(unknown(1)) is False    # 初回は基準
assert d(unknown(1)) is False
assert d(unknown(2)) is True     # 変化 = 押下
print("   ペイロードが変われば押下とみなす")

print()
print("=== cooldown: 1回の押下で複数パケットが飛んでも1回 ===")
d = bt.PressDetector(cooldown=3.0)
d(unknown(1))
presses = sum(d(unknown(n)) for n in (2, 3, 4, 5))
print(f"   4回連続で変化 -> 押下と数えた回数: {presses}")
assert presses == 1

print()
print("=== メーカー固有データの変化も拾う ===")
d = bt.PressDetector(cooldown=0)
d(Adv({FD3D: b"\x62\x00"}, {proto.SWITCHBOT_COMPANY_ID: b"\x01\x02"}))
assert d(Adv({FD3D: b"\x62\x00"}, {proto.SWITCHBOT_COMPANY_ID: b"\x01\x03"})) is True
print("   サービスデータが同じでもメーカー固有データの変化で検知")

print()
print("=== タイマー: 押し直すと再設定される ===")
async def main():
    played = []
    t = bt.BathTimer("aa:bb:cc:dd:ee:ff", minutes=0.02, audio_opts={}, cooldown=0)
    # 実際には鳴らさず、呼ばれたことだけ記録する
    import announce
    announce.play = lambda **kw: played.append(time.monotonic())

    t._handle(Dev("aa:bb:cc:dd:ee:ff"), unknown(1))   # 基準
    t._handle(Dev("aa:bb:cc:dd:ee:ff"), unknown(2))   # 1回目 -> タイマー開始
    first = t.task
    await asyncio.sleep(0.2)
    t._handle(Dev("aa:bb:cc:dd:ee:ff"), unknown(3))   # 押し直し
    await asyncio.sleep(0)                            # 取り消しが処理されるのを待つ
    assert first.done(), first
    print("   古いタイマーは取り消された")
    await asyncio.sleep(1.5)
    print(f"   アナウンス回数: {len(played)} (1回のはず)")
    assert len(played) == 1

    # 別デバイスは無視する
    t2 = bt.BathTimer("aa:bb:cc:dd:ee:ff", 1, {}, cooldown=0)
    t2._handle(Dev("11:22:33:44:55:66"), unknown(9))
    assert t2.task is None
    print("   別の MAC は無視する")

asyncio.run(main())

print()
print("=== 文面ごとに WAV を使い分ける ===")
import announce
a = announce.wav_path("The bath water is full.", "en", 150)
b = announce.wav_path("The bath water is full.", "en", 120)
c = announce.wav_path("Dinner is ready.", "en", 150)
print("   ", a.name, "\n   ", b.name, "\n   ", c.name)
assert a != b != c and a != c
assert announce.wav_path("The bath water is full.", "en", 150) == a
print()
print("すべて OK")
