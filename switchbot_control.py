#!/usr/bin/env python3
# coding: utf-8
"""SwitchBot スマート電球 (Color Bulb) を BLE 接続して制御する。

これまでのスクリプトはアドバタイズを受信するだけでしたが、本スクリプトは
**BLE 接続してコマンドを書き込みます**。クラウドもトークンも不要で、ラズパイから
直接、点灯/消灯・明るさ・色を変えられます。

出典: SwitchBot 公式 BLE 仕様書 devicetypes/colorbulb.md
      0x570F4701 set the status and color of the Bulb
      0x570F4801 read the status of Bulb

  ./venv/bin/python switchbot_control.py --mac 80:65:99:9d:ad:de status
  ./venv/bin/python switchbot_control.py --mac 80:65:99:9d:ad:de on
  ./venv/bin/python switchbot_control.py --mac 80:65:99:9d:ad:de off
  ./venv/bin/python switchbot_control.py --mac 80:65:99:9d:ad:de level 30
  ./venv/bin/python switchbot_control.py --mac 80:65:99:9d:ad:de rgb 0 0 255 --level 50
  ./venv/bin/python switchbot_control.py --mac 80:65:99:9d:ad:de cw 2700

--dry-run を付けると、接続せずに送信するバイト列だけを表示します。
"""
import argparse
import asyncio
import logging

# bleak を import する前に、システムの dist-packages を排除する (sitefix.py 参照)
from sitefix import strip_dist_packages

_STRIPPED = strip_dist_packages()

from bleak import BleakClient, BleakScanner  # noqa: E402

log = logging.getLogger("switchbot")

# 通信用の characteristic (仕様書 "BLE communication packet basic format")
RX_UUID = "cba20002-224d-11e6-9fb8-0002a5d5c51b"   # 端末 -> デバイス (書き込み)
TX_UUID = "cba20003-224d-11e6-9fb8-0002a5d5c51b"   # デバイス -> 端末 (通知)

MAGIC = b"\x57\x0f"          # マジックナンバー + 拡張コマンド
CMD_SET = b"\x47\x01"        # 0x570F4701 電球の状態と色を設定
CMD_READ = b"\x48\x01"       # 0x570F4801 電球の状態を読む

# RESP Packet の Byte0 (仕様書 "Response status")
RESP_STATUS = {
    0x01: "OK", 0x02: "実行エラー", 0x03: "デバイスがビジー",
    0x04: "プロトコルバージョン非互換", 0x05: "未対応のコマンド",
    0x06: "電池残量不足", 0x07: "デバイスが暗号化されている",
    0x08: "デバイスが暗号化されていない", 0x09: "パスワード誤り",
}
MODE_NAMES = {0x01: "白色", 0x02: "カラー", 0x03: "ダイナミック",
              0x04: "ダイナミックグループ", 0x06: "ミュージック", 0xFF: "指定なし"}


def build_command(action, level=None, rgb=None, cw=None):
    """送信するパケットを組み立てる。

    サブコマンド (Byte2) は仕様書の表より:
      0x01 点灯 / 0x02 消灯 / 0x03 トグル
      0x12 明るさ+RGB / 0x13 明るさ+色温度 / 0x14 明るさ / 0x16 RGB / 0x17 色温度
    """
    if action == "status":
        return MAGIC + CMD_READ

    if action == "on":
        payload = b"\x01"
    elif action == "off":
        payload = b"\x02"
    elif action == "toggle":
        payload = b"\x03"
    elif action == "level":
        payload = bytes([0x14, level])
    elif action == "rgb":
        if level is None:
            payload = bytes([0x16, *rgb])
        else:
            payload = bytes([0x12, level, *rgb])
    elif action == "cw":
        # 色温度は 2700〜6500K。1バイトに収まらないので 2 バイトとして送る。
        # 仕様書の表はバイト位置を明示していないが、実機 (Color Bulb) で
        # 2700 と 6500 の色の違いを確認済み。
        if level is None:
            payload = bytes([0x17]) + cw.to_bytes(2, "big")
        else:
            payload = bytes([0x13, level]) + cw.to_bytes(2, "big")
    else:
        raise ValueError(action)

    return MAGIC + CMD_SET + payload


def describe_response(resp):
    """RESP パケットを読んで表示する。"""
    if not resp:
        return "応答なし"
    status = RESP_STATUS.get(resp[0], f"不明なステータス 0x{resp[0]:02x}")
    lines = [f"ステータス: {status} (0x{resp[0]:02x})"]
    if resp[0] != 0x01 or len(resp) < 3:
        return "\n".join(lines)

    lines.append(f"電源      : {'点灯' if resp[1] & 0x80 else '消灯'}")
    lines.append(f"プリセット: {(resp[1] >> 6) & 1}")
    lines.append(f"明るさ    : {resp[2]} %")
    if len(resp) >= 6:
        lines.append(f"RGB       : {resp[3]}, {resp[4]}, {resp[5]}")
    if len(resp) >= 8:
        lines.append(f"色温度    : {(resp[6] << 8) | resp[7]} K")
    if len(resp) >= 11:
        lines.append(f"モード    : {MODE_NAMES.get(resp[10], f'不明 (0x{resp[10]:02x})')}")
    return "\n".join(lines)


# BlueZ が一過性で返す接続エラー。直前の接続が切れきる前に繋ぎ直すと出やすい。
TRANSIENT_ERRORS = (
    "software caused connection abort",
    "le-connection-abort-by-local",
    "connection abort",
    "device disconnected",
    "not connected",
    "operation already in progress",
    "in progress",
)


def _is_transient(err):
    text = str(err).lower()
    return any(m in text for m in TRANSIENT_ERRORS)


async def _send_once(device, packet, timeout):
    loop = asyncio.get_running_loop()
    answer = loop.create_future()

    def on_notify(_, data):
        if not answer.done():
            answer.set_result(bytes(data))

    async with BleakClient(device) as client:
        await client.start_notify(TX_UUID, on_notify)
        log.info("送信: %s", packet.hex())
        await client.write_gatt_char(RX_UUID, packet, response=False)
        try:
            resp = await asyncio.wait_for(answer, timeout)
        except asyncio.TimeoutError:
            raise RuntimeError(
                "応答がありません。コマンドは届いた可能性がありますが確認できません")
        log.info("受信: %s", resp.hex())
        return resp


async def send_command(mac, packet, timeout=10.0, attempts=3):
    """BLE 接続してコマンドを送り、通知で返る応答を待つ。

    BlueZ は直前の接続が切れきる前に繋ぎ直すと接続を中断することがある。
    一過性のエラーは間隔を空けて再試行する。
    """
    log.info("デバイスを探しています: %s", mac)
    device = await BleakScanner.find_device_by_address(mac, timeout=15.0)
    if device is None:
        raise RuntimeError(
            f"{mac} が見つかりません。電源と電波の届く範囲を確認してください")

    last = None
    for attempt in range(1, attempts + 1):
        try:
            log.info("接続しています (%d/%d)", attempt, attempts)
            return await _send_once(device, packet, timeout)
        except Exception as e:
            last = e
            if not _is_transient(e) or attempt == attempts:
                raise
            wait = 2 * attempt
            log.warning("接続に失敗しました (%s) -- %d秒後に再試行します", e, wait)
            await asyncio.sleep(wait)
    raise last


def parse_args():
    ap = argparse.ArgumentParser(
        description="SwitchBot スマート電球を BLE 接続して制御する",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mac", required=True, help="対象の MAC アドレス")
    ap.add_argument("--level", type=int, help="明るさ 0〜100 (rgb / cw と併用可)")
    ap.add_argument("--dry-run", action="store_true",
                    help="接続せず、送信するバイト列だけ表示する")
    ap.add_argument("--attempts", type=int, default=3,
                    help="接続の再試行回数 (既定 3)")
    sub = ap.add_subparsers(dest="action", required=True, metavar="動作")
    sub.add_parser("status", help="現在の状態を読む")
    sub.add_parser("on", help="点灯")
    sub.add_parser("off", help="消灯")
    sub.add_parser("toggle", help="点灯/消灯を切り替える")
    p = sub.add_parser("level", help="明るさを変える")
    p.add_argument("value", type=int, help="0〜100")
    p = sub.add_parser("rgb", help="色を変える")
    p.add_argument("r", type=int)
    p.add_argument("g", type=int)
    p.add_argument("b", type=int)
    p = sub.add_parser("cw", help="色温度を変える 2700=電球色 6500=昼光色")
    p.add_argument("kelvin", type=int, help="2700〜6500")

    args = ap.parse_args()

    level = args.level
    rgb = cw = None
    if args.action == "level":
        if not 0 <= args.value <= 100:
            ap.error("明るさは 0〜100 で指定してください")
        level = args.value
    elif args.action == "rgb":
        rgb = (args.r, args.g, args.b)
        if not all(0 <= v <= 255 for v in rgb):
            ap.error("R/G/B は 0〜255 で指定してください")
    elif args.action == "cw":
        cw = args.kelvin
        if not 2700 <= cw <= 6500:
            ap.error("色温度は 2700〜6500 で指定してください")
    if level is not None and not 0 <= level <= 100:
        ap.error("明るさは 0〜100 で指定してください")

    return args, level, rgb, cw


async def main():
    args, level, rgb, cw = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    packet = build_command(args.action, level=level, rgb=rgb, cw=cw)

    if args.dry_run:
        print("送信するパケット:", packet.hex())
        print("  ", " ".join(f"{b:02x}" for b in packet))
        return

    if _STRIPPED:
        log.warning("システムの dist-packages を import パスから除外しました: %s",
                    ", ".join(_STRIPPED))

    try:
        resp = await send_command(args.mac, packet, attempts=args.attempts)
    except Exception as e:
        log.error("%s", e)
        raise SystemExit(1)

    print()
    print(describe_response(resp))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
