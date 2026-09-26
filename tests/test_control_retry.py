#!/usr/bin/env python3
# coding: utf-8
"""電球への送信の再試行を、BLE なしで検証する。

実機で起きたこと (2026-09-25 22時〜26日 5時): 8時間にわたり毎時、発見の段階で
失敗し続け、ラズパイの再起動で復旧した。当時のコードは発見を1回しか試さず、
そこで諦めていた。

実行:
    ./venv/bin/python tests/test_control_retry.py
"""
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import switchbot_control as ctl

ctl.log.disabled = True


class Fake:
    """find_device と _send_once の振る舞いを回ごとに差し替える。"""

    found = []          # 各回 find_device が返すもの ("dev" か None)
    sends = []          # 各回 _send_once の振る舞い ("ok" か例外の文字列)
    find_calls = 0
    send_calls = 0

    @classmethod
    def install(cls):
        cls.find_calls = cls.send_calls = 0

        async def find_device(mac, timeout=15.0):
            i = min(cls.find_calls, len(cls.found) - 1)
            cls.find_calls += 1
            return cls.found[i]

        async def send_once(device, packet, timeout):
            i = min(cls.send_calls, len(cls.sends) - 1)
            cls.send_calls += 1
            mode = cls.sends[i]
            if mode != "ok":
                raise RuntimeError(mode)
            return bytes.fromhex("018050ffb4000000ffff02")

        ctl.find_device = find_device
        ctl._send_once = send_once


async def run(attempts=3):
    return await ctl.send_command("80:65:99:9d:ad:de", b"\x57\x0f\x47\x01\x02",
                                  attempts=attempts)


async def main():
    orig_sleep = asyncio.sleep
    asyncio.sleep = lambda s: orig_sleep(0)     # 待ち時間は飛ばす

    print("=== 発見に失敗したら、発見からやり直す ===")
    Fake.found = [None, None, "dev"]            # 3回目でやっと見つかる
    Fake.sends = ["ok"]
    Fake.install()
    resp = await run()
    print(f"   発見の試行 {Fake.find_calls}回 / 送信 {Fake.send_calls}回 -> 成功")
    assert Fake.find_calls == 3 and Fake.send_calls == 1
    assert resp.hex().startswith("0180")

    print()
    print("=== 毎回見つからなければ、回数を使い切って諦める ===")
    Fake.found = [None]
    Fake.sends = ["ok"]
    Fake.install()
    try:
        await run(attempts=3)
        raise AssertionError("見つからないのに成功している")
    except ctl.DeviceNotFound as e:
        print(f"   発見の試行 {Fake.find_calls}回 -> {e}")
        assert Fake.find_calls == 3, Fake.find_calls

    print()
    print("=== 接続が一過性に失敗した場合も、発見からやり直す ===")
    Fake.found = ["dev"]
    Fake.sends = ["[org.bluez.Error.Failed] Software caused connection abort", "ok"]
    Fake.install()
    await run()
    print(f"   発見 {Fake.find_calls}回 / 送信 {Fake.send_calls}回 -> 成功")
    assert Fake.find_calls == 2 and Fake.send_calls == 2

    print()
    print("=== 一過性でないエラーは即座に失敗させる (無駄に待たない) ===")
    Fake.found = ["dev"]
    Fake.sends = ["Characteristic not found"]
    Fake.install()
    try:
        await run()
        raise AssertionError("例外が伝播していない")
    except RuntimeError as e:
        print(f"   発見 {Fake.find_calls}回 / 送信 {Fake.send_calls}回 -> {e}")
        assert Fake.find_calls == 1 and Fake.send_calls == 1

    print()
    print("=== 1回目で通れば、余計な試行はしない ===")
    Fake.found = ["dev"]
    Fake.sends = ["ok"]
    Fake.install()
    await run()
    print(f"   発見 {Fake.find_calls}回 / 送信 {Fake.send_calls}回")
    assert Fake.find_calls == 1 and Fake.send_calls == 1

    asyncio.sleep = orig_sleep


asyncio.run(main())
print()
print("すべて OK")
