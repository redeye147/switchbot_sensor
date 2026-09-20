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

    # 確認音は押してすぐではなく ack_delay 秒後。本編は押してから所定の時間後。
    spoken = []
    announce.play = lambda **kw: spoken.append((round(time.monotonic() - t0, 1), kw["message"]))
    t3 = bt.BathTimer("aa:bb:cc:dd:ee:ff", minutes=1.0 / 60, cooldown=0,   # 本編は 1.0秒後
                      audio_opts={"message": "full"}, ack_opts={"message": "ack"},
                      ack_delay=0.4)
    t0 = time.monotonic()
    t3._handle(Dev("aa:bb:cc:dd:ee:ff"), unknown(1))
    t3._handle(Dev("aa:bb:cc:dd:ee:ff"), unknown(2))
    await asyncio.sleep(0.2)
    print(f"   0.2秒時点: {spoken} (まだ鳴らない)")
    assert spoken == [], spoken
    await asyncio.sleep(0.4)
    print(f"   0.6秒時点: {spoken}")
    assert [m for _, m in spoken] == ["ack"], spoken
    await asyncio.sleep(0.8)
    print(f"   1.4秒時点: {spoken}")
    assert [m for _, m in spoken] == ["ack", "full"], spoken
    # 本編は確認音の遅延を差し引いた時刻に鳴る (押下から 1.0秒後のまま)
    full_at = [t for t, m in spoken if m == "full"][0]
    print(f"   本編は押下から {full_at}秒後 (1.0秒の想定)")
    assert 0.9 <= full_at <= 1.3, full_at

    # 確認音が鳴る前に押し直したら、確認音ごと取り消される
    spoken.clear()
    t0 = time.monotonic()
    t5 = bt.BathTimer("aa:bb:cc:dd:ee:ff", minutes=1.0 / 60, cooldown=0,
                      audio_opts={"message": "full"}, ack_opts={"message": "ack"},
                      ack_delay=0.4)
    t5._handle(Dev("aa:bb:cc:dd:ee:ff"), unknown(1))
    t5._handle(Dev("aa:bb:cc:dd:ee:ff"), unknown(2))
    await asyncio.sleep(0.2)
    t5._handle(Dev("aa:bb:cc:dd:ee:ff"), unknown(3))   # 確認音が鳴る前に押し直し
    await asyncio.sleep(1.6)
    print(f"   押し直し後: {[m for _, m in spoken]}")
    assert [m for _, m in spoken] == ["ack", "full"], spoken   # 二重に鳴らない

    # --no-ack 相当なら確認音は鳴らず、本編の時刻もずれない
    spoken.clear()
    t0 = time.monotonic()
    t4 = bt.BathTimer("aa:bb:cc:dd:ee:ff", minutes=1.0 / 60, cooldown=0,
                      audio_opts={"message": "full"}, ack_opts=None, ack_delay=0.4)
    t4._handle(Dev("aa:bb:cc:dd:ee:ff"), unknown(1))
    t4._handle(Dev("aa:bb:cc:dd:ee:ff"), unknown(2))
    await asyncio.sleep(0.6)
    assert spoken == [], spoken
    await asyncio.sleep(0.8)
    print(f"   --no-ack: {spoken}")
    assert [m for _, m in spoken] == ["full"], spoken
    assert 0.9 <= spoken[0][0] <= 1.3, spoken

    # ack_delay が待ち時間より長くても、本編が先に鳴ったりしない
    capped = bt.BathTimer("aa:bb", minutes=1.0 / 60, cooldown=0,
                          audio_opts={}, ack_opts={"message": "ack"}, ack_delay=99)
    print(f"   ack_delay=99 -> {capped.ack_delay} に丸められる")
    assert capped.ack_delay == capped.delay
    print("   --no-ack では確認音なし")

    # 別デバイスは無視する
    t2 = bt.BathTimer("aa:bb:cc:dd:ee:ff", 1, {}, cooldown=0)
    t2._handle(Dev("11:22:33:44:55:66"), unknown(9))
    assert t2.task is None
    print("   別の MAC は無視する")

asyncio.run(main())

print()
print("=== 受信が途絶えたらスキャナを作り直す ===")


async def watchdog_checks():
    import logging
    logging.basicConfig(level=logging.WARNING, format="   %(levelname)s %(message)s")

    # どの機器からも受信がない -> 作り直しを要求する
    t = bt.BathTimer("aa:bb:cc:dd:ee:ff", 15, {}, cooldown=0)
    restart = asyncio.Event()
    t.last_any = time.monotonic() - 120          # 2分間 無受信
    wd = asyncio.create_task(t._watchdog(restart, stall=60, interval=0.05))
    await asyncio.sleep(0.2)
    print(f"   作り直しを要求: {restart.is_set()}")
    assert restart.is_set()
    wd.cancel()

    # 受信が続いていれば作り直さない
    t2 = bt.BathTimer("aa:bb:cc:dd:ee:ff", 15, {}, cooldown=0)
    restart2 = asyncio.Event()
    wd2 = asyncio.create_task(t2._watchdog(restart2, stall=60, interval=0.05))
    for _ in range(4):
        await asyncio.sleep(0.05)
        t2._handle(Dev("99:99:99:99:99:99"), unknown(1))   # 別機器でも受信は受信
    assert not restart2.is_set()
    print("   他の機器から受信できていれば作り直さない")
    wd2.cancel()

    # 対象だけ受信がない -> 作り直さず警告のみ
    t3 = bt.BathTimer("aa:bb:cc:dd:ee:ff", 15, {}, cooldown=0)
    restart3 = asyncio.Event()
    t3.last_any = time.monotonic()
    t3.last_target = time.monotonic() - 1200     # 20分 対象から受信なし
    wd3 = asyncio.create_task(
        t3._watchdog(restart3, stall=60, target_stall=600, interval=0.05))
    await asyncio.sleep(0.2)
    assert not restart3.is_set(), "対象だけの不通でスキャナを作り直してはいけない"
    assert t3.target_lost_warned
    print("   対象だけ不通なら警告のみ (スキャナは作り直さない)")
    wd3.cancel()

    # 受信が戻ったら回復を記録する
    t3._handle(Dev("aa:bb:cc:dd:ee:ff"), unknown(1))
    assert not t3.target_lost_warned
    print("   受信が回復したら警告状態を解除する")


asyncio.run(watchdog_checks())

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
