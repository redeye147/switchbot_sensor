#!/usr/bin/env python3
# coding: utf-8
"""ボタン特定ロジックの検証。実機のノイズを再現して確かめる。

実行:
    ./venv/bin/python tests/test_learn_button.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import learn_button as lb

# 実測で確認された機器。開閉センサーと人感センサーは経過秒カウンタを持つため、
# ボタンと無関係に数秒おきに変化し続ける。
CONTACT = "c4:88:9c:aa:ab:2f"   # 開閉センサー: 3秒おきに変化
MOTION = "d2:dc:22:fd:6d:d1"    # 人感センサー: 3秒おきに変化
HUBLIKE = "e1:02:b9:ac:98:d5"   # 0x76: 3秒おきに変化
BUTTON = "b0:e9:fe:50:2b:ba"    # 押したときだけ変化する機器
PLUG = "10:00:3b:c1:97:a6"      # ほとんど変化しない

BASELINE = (0.0, 12.0)
WINDOWS = [(18.0, 22.0), (28.0, 32.0), (38.0, 42.0)]


def build():
    rec = lb.Recorder()
    t = 0.0
    n = 0
    # 全機器の初回観測
    for addr, dtype in ((CONTACT, 0x64), (MOTION, 0x73), (HUBLIKE, 0x76),
                        (BUTTON, 0x27), (PLUG, 0x6A)):
        rec.record(addr, dtype, b"\x00", 0.0)

    # ノイズ: 3秒おきにカウンタが動く機器 (50秒ぶん)
    while t < 50.0:
        t += 3.0
        n += 1
        for addr in (CONTACT, MOTION, HUBLIKE):
            rec.record(addr, 0, bytes([n]), t)

    # ボタン: 各押下ウィンドウの中でだけ変化する
    for i, (w0, _) in enumerate(WINDOWS):
        rec.record(BUTTON, 0x27, bytes([i + 1]), w0 + 1.0)
    return rec


rows = build()
result = lb.analyse(rows, BASELINE, WINDOWS)

print(f"{'MAC':20} {'反応':6} {'普段の変化':12} 評価")
for r in result:
    print(f"{r['addr']:20} {r['hits']}/{r['rounds']:<4} "
          f"{r['noise_per_min']:5.1f}回/分   {lb.verdict(r)}")

print()
top = result[0]
assert top["addr"] == BUTTON, f"1位が {top['addr']} になっている"
assert top["hits"] == 3 and top["noise_per_min"] == 0.0
print(f"1位はボタン ({BUTTON})")

# カウンタを持つ機器も全ウィンドウで「反応」してしまうが、ノイズで下位に落ちる
noisy = {r["addr"]: r for r in result if r["addr"] in (CONTACT, MOTION, HUBLIKE)}
for addr, r in noisy.items():
    assert r["hits"] == 3, addr           # たまたま全部で反応している
    assert r["noise_per_min"] > 10        # が、普段から動いている
    assert "普段もよく変化する" in lb.verdict(r)
print("カウンタ機器は「毎回反応するが普段もよく変化する」と判定される")

# 何も変化しない機器は反応なし
plug = next(r for r in result if r["addr"] == PLUG)
assert plug["hits"] == 0 and lb.verdict(plug) == "反応なし"
print("無反応の機器は「反応なし」")

# MAC が毎回変わるボタン: 押すたびに別の機器として出現する
rec3 = lb.Recorder()
rec3.record(CONTACT, 0x64, b"\x00", 0.0)
for i, (w0, _) in enumerate(WINDOWS):
    rec3.record(f"7a:bb:cc:dd:ee:{i:02x}", None, b"\x01", w0 + 0.5, name="Remote")
r3 = lb.analyse(rec3, BASELINE, WINDOWS)
fresh = lb.new_during_press(r3)
print()
print("MAC が毎回変わる場合:")
for r in fresh:
    print(f"   {r['addr']}  name={r['name']}  {lb.verdict(r)}")
assert len(fresh) == 3, fresh
assert all(r["name"] == "Remote" for r in fresh)
print("押すたびに別 MAC で現れても、3件すべて拾える")

# SwitchBot 以外の機器も記録対象 (機種バイトは None)
assert all(r["type"] is None for r in fresh)

# 押したときだけ電波を出すボタン (普段は圏外) も拾えること
rec2 = lb.Recorder()
rec2.record(CONTACT, 0x64, b"\x00", 0.0)
rec2.record(BUTTON, 0x27, b"\x01", WINDOWS[0][0] + 0.5)   # 押下中に初登場
r2 = lb.analyse(rec2, BASELINE, WINDOWS)
found = next(r for r in r2 if r["addr"] == BUTTON)
assert found["appeared_on_press"] and "押したときだけ現れた" in lb.verdict(found)
print("押したときだけ現れる機器も検出できる")

print()
print("すべて OK")
