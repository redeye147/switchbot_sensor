#!/usr/bin/env python3
# coding: utf-8
"""SwitchBot デバイスのサービスデータ解析。

bleak 版と bluepy 版で共有する。バイト配置は SwitchBot 公式の BLE 仕様書
https://github.com/OpenWonderLabs/SwitchBotAPI-BLE
の devicetypes/ 以下に準拠する。

  開閉センサー (contactsensor.md)  parse_contact()
  人感センサー (motionsensor.md)   parse_motion()
  カーテン     (curtain.md)       parse_curtain()
  プラグミニ   (plugmini.md)      parse_plug()   ※メーカー固有データ側
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



# ------------------------------------------------------------------- カーテン

# カーテンの種別。'c' = 常時アドバタイズモード、'C' = ペアリングモード。
# Curtain 3 は別形式 (種別 '[' 0x5B / '{' 0x7B, curtain3.md) のため対象外。
DEVICE_TYPE_CURTAIN = 0x63          # 'c'
DEVICE_TYPE_CURTAIN_PAIRING = 0x43  # 'C'


def parse_curtain(payload: bytes):
    """SwitchBot カーテンのサービスデータを解析する。対象外なら None。

    出典: devicetypes/curtain.md ("Curtain Broadcast Package")

        [0] bit[6:0] デバイス種別 'c'(0x63)=常時アドバタイズ / 'C'(0x43)=ペアリング
        [1] bit7     接続を許可しているか
            bit6     キャリブレーション済みか (0 なら要調整)
            bit[5:0] 未使用
        [2] bit[6:0] バッテリー残量 %
        [3] bit7     動作状態  0=停止 / 1=動作中
            bit[6:0] 現在位置 %
        [4] bit[7:4] 明るさレベル (1〜10)
            bit[3:0] デバイスチェーン数

    位置 % の向き (0 が全開か全閉か) は仕様書に明記がない。実機でカーテンを
    動かして確かめること。一般には 0=全開 / 100=全閉 とされる。

    Curtain 3 は種別バイトもバイト配置も異なる (curtain3.md) ため解析しない。
    """
    if len(payload) < 5:
        return None
    if (payload[0] & 0x7F) not in (DEVICE_TYPE_CURTAIN, DEVICE_TYPE_CURTAIN_PAIRING):
        return None

    return {
        "connectable":  (payload[1] >> 7) & 1,             # 接続許可
        "calibrated":   (payload[1] >> 6) & 1,             # 0 なら要キャリブレーション
        "battery":       payload[2] & 0b01111111,          # 残量 %
        "isMoving":     (payload[3] >> 7) & 1,             # 動作中=1
        "position":      payload[3] & 0b01111111,          # 現在位置 %
        "lightLevel":   (payload[4] >> 4) & 0b1111,        # 明るさ 1..10
        "deviceChain":   payload[4] & 0b1111,              # チェーン数
    }


# ----------------------------------------------------------------- プラグミニ

# プラグミニの種別 (サービスデータ側)。JP と US で別の文字が割り当てられている。
DEVICE_TYPE_PLUG_JP = 0x6A          # 'j'
DEVICE_TYPE_PLUG_JP_PAIRING = 0x4A  # 'J'
DEVICE_TYPE_PLUG_US = 0x67          # 'g'
DEVICE_TYPE_PLUG_US_PAIRING = 0x47  # 'G'

# BLE のメーカー固有データに使われる SwitchBot の company ID
SWITCHBOT_COMPANY_ID = 0x0969

# 消費電力の生値をワットに直す係数。0.1W 単位という前提 (実機で要確認)。
POWER_UNIT_W = 0.1


def parse_plug(payload: bytes):
    """SwitchBot プラグミニのメーカー固有データを解析する。対象外なら None。

    他機種と異なり、プラグミニは **サービスデータではなくメーカー固有データ**
    (company ID 0x0969) に状態を載せる。

    出典: devicetypes/plugmini.md ("Plug Mini Broadcast Message")

    仕様書は company ID を含む生の AD ペイロードで位置を数えているが、bleak の
    adv.manufacturer_data[0x0969] は company ID の 2 バイトを除いた中身を返す。
    そのため以下の位置は仕様書の Byte 番号から 2 を引いたものになる。

        [0]〜[5]   デバイスの MAC アドレス (ビッグエンディアン)
        [6]        シーケンス番号 1〜255 (更新のたびに増加し 255 の次は 1)
        [7]        電源状態  0x00=OFF / 0x80=ON
        [8] bit0   ディレイ設定あり
            bit1   タイマー設定あり
            bit2   UTC 時刻が同期済み
        [9]        接続中の Wi-Fi の RSSI (下記の注意を参照)
        [10] bit7     過負荷 (15A 超)
             bit[6:0] 消費電力の上位ビット
        [11]          消費電力の下位8ビット

    消費電力の単位は仕様書に明記がない。一般には 0.1W 単位とされるため
    powerW を併記するが、既知の負荷をつないで確認すること。

    [9] について: 仕様書は Wi-Fi の RSSI としているが、実機 (種別 0x6A) は
    45〜48 という正の値を返す。RSSI は 0 以下にしかならないため、この機種では
    別の意味 (0〜100 の信号強度など) と思われる。生値を wifiRssiRaw に、dBm と
    解釈できる場合のみ wifiRssiDbm に入れる。

    なお SwitchBot は全機種が company ID 0x0969 のメーカー固有データを出して
    おり、先頭6バイトも一様に自分の MAC である。そのため長さや MAC 一致では
    機種を判別できない。判別は service_device_type() で行うこと。
    """
    if len(payload) < 12:
        return None

    power_raw = ((payload[10] & 0b01111111) << 8) | payload[11]

    return {
        "mac":        ":".join(f"{b:02x}" for b in payload[0:6]),
        "sequence":    payload[6],
        "isOn":        1 if payload[7] == 0x80 else 0,
        "stateByte":   payload[7],                     # 0x00 / 0x80 以外が来たとき用
        "hasDelay":    payload[8] & 0b001,
        "hasTimer":   (payload[8] & 0b010) >> 1,
        "utcSynced":  (payload[8] & 0b100) >> 2,
        # 仕様書は [9] を Wi-Fi RSSI とするが、実機 (種別 0x6A) は 45〜48 という
        # 正の値を返す。dBm ではあり得ないため、生値と dBm 解釈を分けて持つ。
        "wifiRssiRaw": payload[9],
        "wifiRssiDbm": payload[9] - 256 if payload[9] > 127 else None,
        "isOverload": (payload[10] >> 7) & 1,          # 15A 超
        "powerRaw":    power_raw,                      # 生値
        "powerW":      round(power_raw * POWER_UNIT_W, 1),
    }

# ------------------------------------------------------------- 機種の判別

# SwitchBot がサービスデータに使う 16bit UUID (0xFD3D と旧来の 0x000D)。
# Bot V6.4 / Curtain V4.6 / Meter V2.7 以降で 0x000D から 0xFD3D に変更された。
SWITCHBOT_SERVICE_UUIDS = {
    "0000fd3d-0000-1000-8000-00805f9b34fb",
    "0000000d-0000-1000-8000-00805f9b34fb",
}

# 公式の機種一覧 (SwitchBotAPI-BLE の README "Device Types")。
# 大文字はペアリングモード、小文字は常時アドバタイズモードの対になっている。
DEVICE_TYPE_NAMES = {
    0x48: "Bot",
    0x54: "温湿度計 (Meter)",
    0x65: "加湿器 (Humidifier)",
    0x63: "カーテン (Curtain)",
    0x7B: "カーテン 3 (Curtain 3)",
    0x73: "人感センサー (Motion Sensor)",
    0x64: "開閉センサー (Contact Sensor)",
    0x75: "スマート電球 (Color Bulb)",
    0x72: "テープライト (LED Strip Light)",
    0x6F: "スマートロック (Smart Lock)",
    0x67: "プラグミニ (Plug Mini)",
    0x69: "温湿度計プラス (Meter Plus)",
    # 以下は公式一覧に無い。実機で観測される値を参考として持つ。
    0x6A: "プラグミニ JP ? (公式一覧に無い種別)",
}


def service_device_type(service_data):
    """アドバタイズのサービスデータから機種バイトを取り出す。無ければ None。

    service_data は bleak の adv.service_data (UUID 文字列 -> bytes) を想定する。
    """
    for uuid, payload in service_data.items():
        if uuid.lower() in SWITCHBOT_SERVICE_UUIDS and payload:
            return payload[0] & 0x7F
    return None


# ------------------------------------------------------------------- 共通処理

# これを下回ったら交換を促す残量 (%)
LOW_BATTERY_THRESHOLD = 20


def battery_note(battery):
    """バッテリー残量に添える注記を返す。注記不要なら空文字。

    開閉センサーの一部個体はアドバタイズに残量を載せず常に 0 を返す。これを
    「残量 0%」として警告すると毎回鳴り続けるため、0 は未取得として扱う。
    """
    if battery == 0:
        return "  ※この個体は残量を送信していません"
    if battery <= LOW_BATTERY_THRESHOLD:
        return f"  ※残り少なくなっています (閾値 {LOW_BATTERY_THRESHOLD}%)"
    return ""
