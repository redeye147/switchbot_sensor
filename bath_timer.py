#!/usr/bin/env python3
# coding: utf-8
"""SwitchBot のボタンを押したら、一定時間後に音声でお知らせする。

    ボタン押下 --> (既定 15分) --> スピーカーから英語アナウンス

お風呂にお湯を張り始めるときにボタンを押しておくと、15分後に
「The bath water is full.」と読み上げます。

  # 1. まずボタンの見つけ方を調べる (押すたびに何が変わるか表示する)
  ./venv/bin/python bath_timer.py --learn

  # 2. 音が出るか確認する
  ./venv/bin/python bath_timer.py --test-audio

  # 3. 運用
  ./venv/bin/python bath_timer.py --mac xx:xx:xx:xx:xx:xx

SwitchBot のリモートボタンは公式 BLE 仕様書に記載がないため、押下の検知は
アドバタイズの中身が変わったことをもって行う (--learn で確認できる)。
開閉センサーのように仕様が分かっている機種は、その機種の押下カウンタを使う。
"""
import argparse
import asyncio
import logging
import time

# bleak を import する前に、システムの dist-packages を排除する (sitefix.py 参照)
from sitefix import strip_dist_packages

_STRIPPED = strip_dist_packages()

from bleak import BleakScanner  # noqa: E402

import announce  # noqa: E402
from switchbot_protocol import (  # noqa: E402
    DEVICE_TYPE_CONTACT,
    DEVICE_TYPE_CONTACT_PAIRING,
    DEVICE_TYPE_NAMES,
    SWITCHBOT_COMPANY_ID,
    parse_contact,
    service_device_type,
)

log = logging.getLogger("switchbot")

DEFAULT_MESSAGE = "The bath water is full."
CONTACT_TYPES = {DEVICE_TYPE_CONTACT, DEVICE_TYPE_CONTACT_PAIRING}


def payload_of(adv):
    """押下判定に使うバイト列。サービスデータとメーカー固有データを連結する。"""
    parts = [payload for _, payload in sorted(adv.service_data.items())]
    md = adv.manufacturer_data.get(SWITCHBOT_COMPANY_ID)
    if md is not None:
        parts.append(md)
    return b"".join(parts)


class PressDetector:
    """アドバタイズの変化から「ボタンが押された」を判定する。

    開閉センサーのように押下カウンタが分かっている機種はそれを使い、それ以外は
    ペイロードが変化したことをもって押下とみなす。どちらの場合も、1回の押下で
    複数のパケットが飛ぶため cooldown 秒のあいだは重複を無視する。
    """

    def __init__(self, cooldown=3.0):
        self.cooldown = cooldown
        self.prev_payload = None
        self.prev_count = None
        self.last_press = 0.0

    def __call__(self, adv):
        dtype = service_device_type(adv.service_data)
        payload = payload_of(adv)

        pressed = False
        if dtype in CONTACT_TYPES:
            # 仕様が分かっている機種は押下カウンタで判定する (1..15 で循環)
            for sd in adv.service_data.values():
                data = parse_contact(sd)
                if data is None:
                    continue
                count = data["buttonCount"]
                if self.prev_count is not None and (count - self.prev_count) % 15:
                    pressed = True
                self.prev_count = count
        else:
            # 仕様が分からない機種はペイロードの変化を押下とみなす
            if self.prev_payload is not None and payload != self.prev_payload:
                pressed = True

        self.prev_payload = payload

        if not pressed:
            return False
        now = time.monotonic()
        if now - self.last_press < self.cooldown:
            return False            # 同じ押下による連続パケット
        self.last_press = now
        return True


class BathTimer:
    """ボタン押下を待ち、一定時間後にアナウンスする。"""

    def __init__(self, mac, minutes, audio_opts, cooldown=3.0):
        self.mac = mac.lower()
        self.delay = minutes * 60
        self.audio = audio_opts
        self.detect = PressDetector(cooldown)
        self.task = None

    def _handle(self, device, adv):
        if device.address.lower() != self.mac:
            return
        if not self.detect(adv):
            return
        self._schedule()

    def _schedule(self):
        if self.task and not self.task.done():
            self.task.cancel()
            log.info("押し直されました。タイマーを再設定します")
        self.task = asyncio.create_task(self._countdown())

    async def _countdown(self):
        due = time.strftime("%H:%M", time.localtime(time.time() + self.delay))
        log.info("ボタンが押されました。%d分後 (%s頃) にお知らせします",
                 self.delay // 60, due)
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            return
        try:
            await asyncio.to_thread(announce.play, **self.audio)
        except announce.AudioError as e:
            log.error("アナウンスできませんでした: %s", e)

    async def run(self):
        log.info("%s のボタンを待っています (Ctrl-C で終了)", self.mac)
        while True:
            try:
                async with BleakScanner(self._handle):
                    await asyncio.Future()      # 押されるまで待ち続ける
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("BLE スキャンが停止しました (%s) -- 5秒後に再開します", e)
                await asyncio.sleep(5)


async def learn(mac=None, seconds=60):
    """押下を特定するための観察モード。変化したデバイスだけを表示する。"""
    seen = {}
    target = mac.lower() if mac else None

    def cb(device, adv):
        addr = device.address.lower()
        if target and addr != target:
            return
        dtype = service_device_type(adv.service_data)
        if dtype is None:
            return                              # SwitchBot 以外は無視
        payload = payload_of(adv)
        prev = seen.get(addr)
        seen[addr] = payload
        if prev is None:
            name = DEVICE_TYPE_NAMES.get(dtype, f"不明な機種 0x{dtype:02x}")
            print(f"{time.strftime('%H:%M:%S')}  {addr}  {name}")
            print(f"           初回: {payload.hex()}")
        elif prev != payload:
            print(f"{time.strftime('%H:%M:%S')}  {addr}  ★変化")
            print(f"           前回: {prev.hex()}")
            print(f"           今回: {payload.hex()}")
            diff = [i for i, (a, b) in enumerate(zip(prev, payload)) if a != b]
            print(f"           変化したバイト位置: {diff}", flush=True)

    print(f"--- {seconds}秒間、SwitchBot 機器を観察します ---")
    print("この間にボタンを何度か押してください。★変化 が出た機器がそれです。")
    print()
    async with BleakScanner(cb):
        await asyncio.sleep(seconds)
    print()
    print(f"--- 終了。観察した機器 {len(seen)}台 ---")


def parse_args():
    ap = argparse.ArgumentParser(
        description="SwitchBot のボタンを押したら一定時間後に音声でお知らせする")
    ap.add_argument("--mac", help="トリガーにするデバイスの MAC アドレス")
    ap.add_argument("--minutes", type=float, default=15,
                    help="押されてから知らせるまでの分数 (既定 15)")
    ap.add_argument("--message", default=DEFAULT_MESSAGE,
                    help=f"読み上げる文面 (既定: {DEFAULT_MESSAGE!r})")
    ap.add_argument("--voice", default="en", help="espeak-ng の音声 (既定 en)")
    ap.add_argument("--speed", type=int, default=150, help="読み上げ速度 (既定 150)")
    ap.add_argument("--repeat", type=int, default=2, help="読み上げ回数 (既定 2)")
    ap.add_argument("--device", help="aplay の出力先 (例 plughw:1,0)")
    ap.add_argument("--cooldown", type=float, default=3.0,
                    help="1回の押下とみなす秒数 (既定 3)")
    ap.add_argument("--learn", action="store_true",
                    help="押下を特定するための観察モード")
    ap.add_argument("--learn-seconds", type=int, default=60, help="観察する秒数")
    ap.add_argument("--test-audio", action="store_true",
                    help="待たずにアナウンスを再生して終了する")
    return ap, ap.parse_args()


async def main():
    ap, args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    audio_opts = dict(message=args.message, voice=args.voice, speed=args.speed,
                      repeat=args.repeat, device=args.device)

    if args.test_audio:
        try:
            announce.play(**audio_opts)
        except announce.AudioError as e:
            log.error("%s", e)
            raise SystemExit(1)
        return

    if args.learn:
        await learn(args.mac, args.learn_seconds)
        return

    if not args.mac:
        ap.error("--mac を指定してください (--learn でボタンを特定できます)")

    if _STRIPPED:
        log.warning("システムの dist-packages を import パスから除外しました: %s",
                    ", ".join(_STRIPPED))

    # 音声を先に用意しておく。15分待ってから失敗するのを避ける。
    try:
        announce.build_wav(args.message, args.voice, args.speed)
    except announce.AudioError as e:
        log.error("%s", e)
        raise SystemExit(1)

    await BathTimer(args.mac, args.minutes, audio_opts, args.cooldown).run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
