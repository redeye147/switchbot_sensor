#!/usr/bin/env python3
# coding: utf-8
"""SwitchBot デバイスのサービスデータ解析。

bleak 版と bluepy 版で共有する。バイト配置は SwitchBot 公式の BLE 仕様書
https://github.com/OpenWonderLabs/SwitchBotAPI-BLE
の devicetypes/ 以下に準拠する。

  開閉センサー (contactsensor.md)  parse_contact()
  人感センサー (motionsensor.md)   parse_motion()
"""

# 開閉センサーの種別。'd' = 常時アドバタイズモード、'D' = ペアリングモード。
DEVICE_TYPE_CONTACT = 0x64          # 'd'
DEVICE_TYPE_CONTACT_PAIRING = 0x44  # 'D'

# [3] bit[1:2] のドア状態
DOOR_CLOSED, DOOR_OPEN, DOOR_TIMEOUT = 0, 1, 2
DOOR_STATE_NAMES = {DOOR_CLOSED: "閉", DOOR_OPEN: "開", DOOR_TIMEOUT: "開けっ放し"}

# SwitchBot がサービスデータに使う 16bit UUID (0xFD3D と旧来の 0x000D)


def parse_contact(payload: bytes):
    """SwitchBot 開閉センサーのサービスデータを解析する。対象外なら None。

    バイト配置は SwitchBot 公式の BLE 仕様書に準拠する。
    https://github.com/OpenWonderLabs/SwitchBotAPI-BLE
      -> devicetypes/contactsensor.md ("Contact Sensor Broadcast Message")

        [0] bit7     暗号化の有無
            bit[6:0] デバイス種別 'd'(0x64)=常時アドバタイズ / 'D'(0x44)=ペアリング
        [1] bit7     スコープテスト済みか
            bit6     PIR State  1=誰か動いている / 0=動きなし
            bit[5:0] 未使用
        [2] bit[6:0] バッテリー残量 %
        [3] bit7     PIR 経過秒の最上位ビット (x65536)
            bit6     HAL 経過秒の最上位ビット (x65536)
            bit[1:2] Hal State  0=閉 / 1=開 / 2=開いたままタイムアウト
            bit0     Light Level  0=暗い / 1=明るい
        [4][5]       最後の PIR (人感) 検知からの経過秒 下位16bit (ビッグエンディアン)
        [6][7]       最後の HAL (ドア磁石) 検知からの経過秒 下位16bit (同上)
        [8] bit[6:7] 入室回数カウンタ (1..3 で循環)
            bit[4:5] 外出回数カウンタ (1..3 で循環)
            bit[0:3] ボタン押下カウンタ (1..15 で循環)

    ドア状態は bit1 と bit2 の独立したフラグではなく 2bit の値である点に注意。
    値 2 (開いたままタイムアウト) はドアが「開いている」状態を指す。
    """
    if len(payload) < 9:
        return None
    if (payload[0] & 0x7F) not in (DEVICE_TYPE_CONTACT, DEVICE_TYPE_CONTACT_PAIRING):
        return None

    door_state = (payload[3] >> 1) & 0b11

    return {
        "encrypted":     (payload[0] >> 7) & 1,            # 1 なら以降の値は復号が必要
        "isMotion":      (payload[1] >> 6) & 1,            # PIR: 誰か動いている=1
        "battery":        payload[2] & 0b01111111,         # 残量 % (下記の注記参照)
        "isIlluminance":  payload[3] & 0b00000001,         # 明るい=1 / 暗い=0
        "doorState":      door_state,                      # 0=閉 1=開 2=開けっ放し
        "isOpen":         1 if door_state != DOOR_CLOSED else 0,
        "isLeaveOpen":    1 if door_state == DOOR_TIMEOUT else 0,
        # 経過秒は 17bit。最上位ビットが [3] にあるため 65535 を超えても正しく読める。
        "secSincePir":   (((payload[3] >> 7) & 1) << 16) | (payload[4] << 8) | payload[5],
        "secSinceHal":   (((payload[3] >> 6) & 1) << 16) | (payload[6] << 8) | payload[7],
        "entranceCount": (payload[8] >> 6) & 0b11,         # 入室回数 1..3
        "goOutCount":    (payload[8] >> 4) & 0b11,         # 外出回数 1..3
        "buttonCount":    payload[8] & 0b00001111,         # ボタン押下 1..15
    }


# --------------------------------------------------------------- 人感センサー

# 人感センサーの種別。's' = 常時アドバタイズモード、'S' = ペアリングモード。
DEVICE_TYPE_MOTION = 0x73          # 's'
DEVICE_TYPE_MOTION_PAIRING = 0x53  # 'S'

# [5] bit[3:2] 検知距離
SENSING_DISTANCE_NAMES = {0: "長 (Long)", 1: "中 (Middle)", 2: "短 (Short)", 3: "予約"}

# [5] bit[1:0] 明るさ。00 と 11 は予約値。
LIGHT_DARK, LIGHT_BRIGHT = 1, 2
LIGHT_LEVEL_NAMES = {0: "予約", LIGHT_DARK: "暗い", LIGHT_BRIGHT: "明るい", 3: "予約"}


def parse_motion(payload: bytes):
    """SwitchBot 人感センサーのサービスデータを解析する。対象外なら None。

    出典: devicetypes/motionsensor.md ("Motion Sensor Broadcast Message")

        [0] bit[6:0] デバイス種別 's'(0x73)=常時アドバタイズ / 'S'(0x53)=ペアリング
        [1] bit7     スコープテスト済みか
            bit6     PIR State  1=誰か動いている / 0=動きなし
            bit[5:0] 未使用
        [2] bit[6:0] バッテリー残量 %
        [3][4]       最後の PIR 検知からの経過秒 下位16bit ([3]=上位8bit [4]=下位8bit)
        [5] bit7     同経過秒の最上位ビット (x65536)
            bit6     予約
            bit5     LED 設定  0=無効 / 1=有効
            bit4     IoT 設定  0=無効 / 1=有効
            bit[3:2] 検知距離  0=長 / 1=中 / 2=短
            bit[1:0] 明るさ    1=暗い / 2=明るい (0 と 3 は予約値)

    経過秒は 17bit。最上位ビットが [5] にあるため、[4] だけを読むと 255 秒で
    一周してしまう点に注意。
    """
    if len(payload) < 6:
        return None
    if (payload[0] & 0x7F) not in (DEVICE_TYPE_MOTION, DEVICE_TYPE_MOTION_PAIRING):
        return None

    light = payload[5] & 0b00000011

    return {
        "isMotion":      (payload[1] >> 6) & 1,            # 誰か動いている=1
        "isON":          (payload[1] >> 6) & 1,            # 旧名 (isMotion と同じ)
        "battery":        payload[2] & 0b01111111,         # 残量 %
        # 経過秒は 17bit。[5] bit7 が最上位。
        "secSincePir":   (((payload[5] >> 7) & 1) << 16) | (payload[3] << 8) | payload[4],
        "ledEnabled":    (payload[5] >> 5) & 1,            # 本体 LED
        "iotEnabled":    (payload[5] >> 4) & 1,            # クラウド連携
        "sensingDistance": (payload[5] >> 2) & 0b11,       # 0=長 1=中 2=短
        "lightLevel":     light,                           # 1=暗い 2=明るい
        # 予約値 (0, 3) のときは判定不能として None を返す
        "isIlluminance":  1 if light == LIGHT_BRIGHT else (0 if light == LIGHT_DARK else None),
    }
