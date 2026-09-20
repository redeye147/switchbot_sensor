#!/usr/bin/env python3
# coding: utf-8
"""BLE スキャンが壊れたときの復旧を、実機で起きた壊れ方で検証する。

実機ログ (2026-09-20 20:45) で起きたのは次の順序である。

    WARNING 73秒間まったく受信がありません。スキャナを作り直します
    WARNING BLE スキャンが停止しました ([org.bluez.Error.Failed] No discovery started)
    (以降 47分間 何も記録されない)

停止処理が失敗したあと、続く開始処理が例外も出さずに固まっていた。

実行:
    ./venv/bin/python tests/test_scan_recovery.py
"""
import asyncio
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import bath_timer as bt

logging.basicConfig(level=logging.WARNING, format="      %(levelname)s %(message)s")

# テストを短時間で回すため、実運用より大幅に短くする
bt.SCAN_START_TIMEOUT = 0.2
bt.SCAN_STOP_TIMEOUT = 0.1
bt.RETRY_DELAY = 0.02


class FakeScanner:
    """1回ごとに挙動を指定できる偽スキャナ。

    behaviour の各要素は (start の動作, stop の動作)。
      "hang"    固まる (タイムアウトの対象)
      "raise"   例外を投げる
      "data"    正常に開始し、パケットを1つ届ける
      "ok"      何もせず正常終了
    """

    behaviour = []
    calls = 0

    def __init__(self, cb):
        self.cb = cb
        self.index = min(FakeScanner.calls, len(FakeScanner.behaviour) - 1)
        FakeScanner.calls += 1

    async def start(self):
        mode = FakeScanner.behaviour[self.index][0]
        if mode == "hang":
            await asyncio.sleep(999)
        elif mode == "raise":
            raise RuntimeError("[org.bluez.Error.Failed] No discovery started")
        elif mode == "data":
            self.cb(type("D", (), {"address": "99:99:99:99:99:99"})(), None)

    async def stop(self):
        if FakeScanner.behaviour[self.index][1] == "raise":
            raise RuntimeError("No discovery started")


def fire_soon(restart, **_):
    """監視役の代わり。すぐ作り直しを要求する。"""
    async def _run():
        await asyncio.sleep(0.02)
        restart.set()
        await asyncio.sleep(999)
    return _run()


async def _runner(timer):
    # SystemExit は BaseException なので、タスクの外に出す前に受け止める。
    try:
        await timer.run()
    except SystemExit as e:
        return e.code
    return "returned"


async def run_until_exit(timer, limit=5.0):
    """run() が SystemExit で終わるまで待つ。終わらなければ None。"""
    try:
        return await asyncio.wait_for(_runner(timer), limit)
    except asyncio.TimeoutError:
        return None


async def main():
    bt.BleakScanner = FakeScanner

    print("=== 開始処理が固まってもタイムアウトする ===")
    FakeScanner.behaviour = [("hang", "ok")]
    FakeScanner.calls = 0
    t = bt.BathTimer("aa:bb", 15, {}, cooldown=0)
    code = await run_until_exit(t)
    print(f"   終了コード {code} / 作り直し {FakeScanner.calls}回")
    assert code == 1, f"固まったまま戻らなかった: {code}"
    assert FakeScanner.calls == bt.MAX_SCAN_FAILURES

    print()
    print("=== 実機と同じ順序: stop が失敗し、その後 start が固まる ===")
    FakeScanner.behaviour = [("data", "raise"), ("hang", "raise")]
    FakeScanner.calls = 0
    t = bt.BathTimer("aa:bb", 15, {}, cooldown=0)
    t._watchdog = fire_soon
    code = await run_until_exit(t)
    print(f"   終了コード {code} / 作り直し {FakeScanner.calls}回")
    assert code == 1, "47分間だまり込む状態を再現してしまった"
    print("   だまり込まず、systemd に処理を渡せる")

    print()
    print("=== 受信が戻れば失敗回数はリセットされる ===")
    # 失敗2回 -> 受信あり -> また失敗。リセットが効けば早期終了しない。
    FakeScanner.behaviour = [("raise", "ok"), ("raise", "ok"), ("data", "ok"),
                             ("raise", "ok"), ("raise", "ok"), ("raise", "ok")]
    FakeScanner.calls = 0
    t = bt.BathTimer("aa:bb", 15, {}, cooldown=0)
    t._watchdog = fire_soon
    code = await run_until_exit(t)
    print(f"   終了コード {code} / 作り直し {FakeScanner.calls}回")
    assert FakeScanner.calls == 6, FakeScanner.calls
    print("   途中の受信でリセットされ、3回で終了していない")

    print()
    print("=== 正常時は終了しない ===")
    FakeScanner.behaviour = [("data", "ok")]
    FakeScanner.calls = 0
    t = bt.BathTimer("aa:bb", 15, {}, cooldown=0)
    code = await run_until_exit(t, limit=0.5)
    print(f"   終了コード {code} (None = 動き続けている)")
    assert code is None


asyncio.run(main())
print()
print("すべて OK")
