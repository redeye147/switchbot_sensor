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
import learn_button  # noqa: E402
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
DEFAULT_ACK = "Timer started."
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

    def __init__(self, mac, minutes, audio_opts, cooldown=3.0, ack_opts=None):
        self.mac = mac.lower()
        self.delay = minutes * 60
        self.audio = audio_opts
        # 押したその場で鳴らす確認音。15分後まで成否が分からないのを避ける。
        self.ack = ack_opts
        self.detect = PressDetector(cooldown)
        self.task = None
        self.recent = []                # 誤った MAC を指定したときに気付くため
        # 受信の途絶を見張るための時刻。BlueZ はスキャンを黙って止めることが
        # あり、起動直後はアダプタがまだ使えないこともある。例外が出ないので
        # 監視しないと「動いているつもりで何も受信していない」状態が続く。
        self.last_any = time.monotonic()
        self.last_target = None
        self.target_lost_warned = False

    def _handle(self, device, adv):
        self.last_any = time.monotonic()
        if device.address.lower() != self.mac:
            return

        if self.last_target is None:
            log.info("センサーを受信しました (%s)", self.mac)
        elif self.target_lost_warned:
            log.info("センサーの受信が回復しました (%s)", self.mac)
        self.last_target = self.last_any
        self.target_lost_warned = False

        if not self.detect(adv):
            return
        self._schedule()

    def _warn_if_too_frequent(self):
        """押下が多すぎるときは、カウンタを持つ機器を指定している可能性を伝える。

        開閉センサーや人感センサーは経過秒カウンタを持ち、ボタンと無関係に
        数秒おきにアドバタイズが変化する。そういう機器を --mac に指定すると
        タイマーが際限なく再設定され、いつまでも鳴らない。
        """
        now = time.monotonic()
        self.recent = [t for t in self.recent if now - t < 120] + [now]
        if len(self.recent) == 6:
            log.warning("2分間に6回も押下を検知しました。--mac の機器が正しいか"
                        "確認してください (経過秒カウンタを持つ機器の可能性)")
            log.warning("--learn でボタンを特定し直せます")

    def _schedule(self):
        self._warn_if_too_frequent()
        if self.task and not self.task.done():
            self.task.cancel()
            log.info("押し直されました。タイマーを再設定します")
        self.task = asyncio.create_task(self._countdown())

    async def _countdown(self):
        due = time.strftime("%H:%M", time.localtime(time.time() + self.delay))
        log.info("ボタンが押されました。%d分後 (%s頃) にお知らせします",
                 self.delay // 60, due)
        if self.ack:
            try:
                await asyncio.to_thread(announce.play, **self.ack)
            except announce.AudioError as e:
                log.error("確認音を鳴らせませんでした: %s", e)
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            return
        try:
            await asyncio.to_thread(announce.play, **self.audio)
        except announce.AudioError as e:
            log.error("アナウンスできませんでした: %s", e)

    async def _watchdog(self, restart, stall=60.0, target_stall=600.0, interval=15.0):
        """受信が途絶えたらスキャナを作り直す。

        stall:        どの機器からも受信がない秒数。これを超えたらスキャナが
                      死んでいるとみなして作り直す。
        target_stall: 対象のセンサーだけ受信がない秒数。こちらはスキャナの
                      問題ではないので、作り直さず警告にとどめる。
        """
        while True:
            await asyncio.sleep(interval)
            now = time.monotonic()

            if now - self.last_any > stall:
                log.warning("%.0f秒間まったく受信がありません。スキャナを作り直します",
                            now - self.last_any)
                restart.set()
                return

            if self.last_target is None:
                if now - self.last_any > target_stall and not self.target_lost_warned:
                    log.warning("他の機器は受信できていますが、%s からは一度も"
                                "受信していません。MAC と電波の届く範囲を確認してください",
                                self.mac)
                    self.target_lost_warned = True
            elif now - self.last_target > target_stall and not self.target_lost_warned:
                log.warning("%s から %.0f分間 受信していません。電池と距離を"
                            "確認してください", self.mac, (now - self.last_target) / 60)
                self.target_lost_warned = True

    async def run(self):
        log.info("%s のボタンを待っています (Ctrl-C で終了)", self.mac)
        while True:
            restart = asyncio.Event()
            watchdog = None
            try:
                self.last_any = time.monotonic()    # 起動直後の誤検知を避ける
                async with BleakScanner(self._handle):
                    watchdog = asyncio.create_task(self._watchdog(restart))
                    await restart.wait()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("BLE スキャンが停止しました (%s) -- 5秒後に再開します", e)
                await asyncio.sleep(5)
            finally:
                if watchdog:
                    watchdog.cancel()
            await asyncio.sleep(1)


async def learn(rounds=learn_button.ROUNDS,
                baseline=learn_button.BASELINE_SECONDS,
                window=learn_button.WINDOW_SECONDS,
                gap=learn_button.GAP_SECONDS):
    """押した時刻との対応からボタンを特定する。

    「変化したら押下」では特定できない。開閉センサーや人感センサーは経過秒
    カウンタを持ち、ボタンと無関係に数秒おきに変化し続けるためである。
    まず押していない時間帯の変化 (ノイズ) を測り、そのうえで指示した数秒間に
    変化した機器を記録して、両者を突き合わせる。
    """
    rec = learn_button.Recorder()

    def cb(device, adv):
        # SwitchBot 以外も記録する。リモートは SwitchBot 形式のサービスデータを
        # 出さない可能性があり、機種で絞ると取り逃がす。
        dtype = service_device_type(adv.service_data)
        payload = payload_of(adv) or _any_payload(adv)
        rec.record(device.address.lower(), dtype, payload, time.monotonic(),
                   name=adv.local_name)

    print("=" * 60)
    print("ボタンを特定します。画面の指示どおりに押してください。")
    print("=" * 60)
    print()

    async with BleakScanner(cb):
        print(f"[1] まず {baseline:.0f} 秒、何も押さずにお待ちください (ノイズの計測)")
        b0 = time.monotonic()
        await asyncio.sleep(baseline)
        b1 = time.monotonic()
        print(f"    計測しました。機器 {len(rec.first_seen)}台 を観測中")
        print()

        windows = []
        for i in range(rounds):
            print(f"[{i + 2}] ★ 今すぐボタンを1回押してください ({i + 1}/{rounds})")
            w0 = time.monotonic()
            await asyncio.sleep(window)
            windows.append((w0, time.monotonic()))
            if i < rounds - 1:
                print(f"    記録しました。{gap:.0f} 秒お待ちください (押さないでください)")
                await asyncio.sleep(gap)
        print("    記録しました。")

    print()
    print("=" * 60)
    print("結果")
    print("=" * 60)
    rows = learn_button.analyse(rec, (b0, b1), windows)
    if not rows:
        print("BLE 機器が見つかりませんでした。Bluetooth が有効か確認してください。")
        return

    appeared = learn_button.new_during_press(rows)
    rates = learn_button.appearance_rates(rec, (b0, b1), windows)
    noisy_area = learn_button.appearances_look_like_noise(rates)

    if appeared:
        print()
        if noisy_area:
            print("■ 押下中に現れた機器 -- ただし背景ノイズと判断しました")
            print(f"    待機中も {rates['idle_per_min']:.0f}台/分 のペースで新しい機器が"
                  f"現れています (押下中は {rates['press_per_min']:.0f}台/分)")
            print("    周囲に MAC アドレスを定期的に変える機器 (スマホ等) が多いためです")
        else:
            print("■ 押したときに初めて現れた機器 (普段は電波を出さないボタンの可能性)")
        for r in appeared[:8]:
            print(f"    {r['addr']}  {_device_label(r)}")
        if len(appeared) > 8:
            print(f"    ... 他 {len(appeared) - 8} 台")
        print()

    print(f"{'MAC':20} {_pad('機種', 30)} {'反応':6} {'普段の変化':11} 評価")
    shown = 0
    for r in rows:
        # 反応ゼロかつ静かな機器が大量にあるので、先頭だけ出す
        if r["hits"] == 0 and not r["appeared_on_press"] and shown >= 12:
            continue
        shown += 1
        hits = f"{r['hits']}/{r['rounds']}"
        noise = f"{r['noise_per_min']:.1f}回/分"
        print(f"{r['addr']:20} {_pad(_device_label(r), 30)} {hits:6} {noise:11} "
              f"{learn_button.verdict(r)}")
    if len(rows) > shown:
        print(f"... 他 {len(rows) - shown} 台 (反応なし)")

    best = rows[0]
    print()
    credible_appearance = best["appeared_on_press"] and not noisy_area
    if credible_appearance or (
            best["hits"] == best["rounds"] and best["noise_per_min"] < 1.0):
        print(f"ボタンはおそらく {best['addr']} です。次のように指定してください:")
        print(f"  ./venv/bin/python bath_timer.py --mac {best['addr']}")
    else:
        print("決め手になる機器がありませんでした。")
        print()
        print("SwitchBot のリモートボタンは、ペアリング済みの Bot やカーテンに")
        print("直接コマンドを送る設計で、周囲に状態を広告しない可能性があります。")
        print("その場合、押下を BLE の受信だけで捉えることはできません。")
        print()
        print("確認してください:")
        print("  - 押すタイミングが指示とずれていないか (★ が出た直後に押す)")
        print("  - リモートがラズパイの電波の届く範囲にあるか")
        print("  - --rounds を増やす、--window を長くする")
        print()
        print("代わりの方法として、開閉センサー本体のボタンが使えます。")
        print("押下が仕様どおり検知でき、実機で確認済みです:")
        print("  ./venv/bin/python bath_timer.py --mac c4:88:9c:aa:ab:2f")


def _pad(text, width):
    """全角を2桁として数え、表示幅を揃える。"""
    import unicodedata
    w = sum(2 if unicodedata.east_asian_width(c) in "FWA" else 1 for c in text)
    return text + " " * max(0, width - w)


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
    ap.add_argument("--ack-message", default=DEFAULT_ACK,
                    help=f"押したときに鳴らす確認の文面 (既定: {DEFAULT_ACK!r})")
    ap.add_argument("--no-ack", action="store_true", help="押したときの確認音を鳴らさない")
    ap.add_argument("--device", help="aplay の出力先 (例 plughw:1,0)")
    ap.add_argument("--cooldown", type=float, default=3.0,
                    help="1回の押下とみなす秒数 (既定 3)")
    ap.add_argument("--learn", action="store_true",
                    help="押した時刻との対応からボタンを特定する")
    ap.add_argument("--rounds", type=int, default=learn_button.ROUNDS,
                    help="--learn で押してもらう回数 (既定 3)")
    ap.add_argument("--window", type=float, default=learn_button.WINDOW_SECONDS,
                    help="--learn で1回の押下を待つ秒数 (既定 4)")
    ap.add_argument("--test-audio", action="store_true",
                    help="待たずにアナウンスを再生して終了する")
    return ap, ap.parse_args()


async def main():
    ap, args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    audio_opts = dict(message=args.message, voice=args.voice, speed=args.speed,
                      repeat=args.repeat, device=args.device)
    ack_opts = None if args.no_ack else dict(
        message=args.ack_message, voice=args.voice, speed=args.speed,
        repeat=1, device=args.device)

    if args.test_audio:
        try:
            announce.play(**audio_opts)
        except announce.AudioError as e:
            log.error("%s", e)
            raise SystemExit(1)
        return

    if args.learn:
        await learn(rounds=args.rounds, window=args.window)
        return

    if not args.mac:
        ap.error("--mac を指定してください (--learn でボタンを特定できます)")

    if _STRIPPED:
        log.warning("システムの dist-packages を import パスから除外しました: %s",
                    ", ".join(_STRIPPED))

    # 音声を先に用意しておく。15分待ってから失敗するのを避ける。
    try:
        announce.build_wav(args.message, args.voice, args.speed)
        if ack_opts:
            announce.build_wav(args.ack_message, args.voice, args.speed)
    except announce.AudioError as e:
        log.error("%s", e)
        raise SystemExit(1)

    await BathTimer(args.mac, args.minutes, audio_opts, args.cooldown,
                    ack_opts=ack_opts).run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
