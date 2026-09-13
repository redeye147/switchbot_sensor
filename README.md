# switchbot_sensor

SwitchBot の BLE センサーを Raspberry Pi から受信するスクリプト集。

SwitchBot デバイスは BLE のアドバタイジングパケットにセンサー値を載せて周囲にばら撒いています。
本リポジトリのスクリプトはそれを**受信するだけ**で、ペアリングも接続もクラウド連携も不要です。

## 対応デバイス

| デバイス | 種別バイト | スクリプト |
|---|---|---|
| 開閉センサー (Contact Sensor) | `0x64` (`'d'`) | `switchbot_contact.py` |
| 人感センサー (Motion Sensor) | `0x73` (`'s'`) | `switchbot_motion.py` |

## クイックスタート

```bash
sudo apt install -y python3-venv bluez
./setup.sh

# 周囲の SwitchBot を探して MAC アドレスを調べる
./venv/bin/python switchbot_contact.py --scan

# 受信開始
./venv/bin/python switchbot_contact.py --mac c4:88:9c:aa:ab:2f   # 開閉センサー
./venv/bin/python switchbot_motion.py  --mac d2:dc:22:fd:6d:d1   # 人感センサー
```

詳細なセットアップと自動起動の設定は [docs/SETUP.md](docs/SETUP.md) を参照。

## ファイル構成

| パス | 内容 |
|---|---|
| `switchbot_contact.py` | **推奨実装**。bleak (BlueZ D-Bus) 版。root 権限不要 |
| `switchbot_motion.py` | 人感センサー用。bleak 版 |
| `switchbot_contact_bluepy.py` | bluepy 版。旧実装互換。root 権限または setcap が必要 |
| `systemd/switchbot-contact.service` | 自動起動用 systemd ユニット |
| `setup.sh` | クリーンな venv を作成。dist-packages の混入を遮断する |
| `verify_env.py` | venv がシステムから隔離されているかを診断する |
| `switchbot_protocol.py` | サービスデータの解析。全スクリプトで共有 |
| `sitefix.py` | 実行時にシステムの dist-packages を import パスから除外する |
| `tests/test_offline.py` | BLE ハードウェアなしで解析ロジックを検証する |
| `docs/SETUP.md` | ラズパイ セットアップ手順とトラブルシューティング |

## 取得できる値

| キー | 意味 |
|---|---|
| `doorState` | `0`=閉 / `1`=開 / `2`=開いたままタイムアウト |
| `isOpen` | 開=1 / 閉=0 (`doorState != 0`) |
| `isLeaveOpen` | 開けっ放し=1 (`doorState == 2`) |
| `isIlluminance` | 明るい=1 / 暗い=0 |
| `isMotion` | 人感 (PIR) で動きを検知=1 |
| `secSinceHal` | 最後のドア開閉からの経過秒 |
| `secSincePir` | 最後の人感検知からの経過秒 |
| `buttonCount` | ボタン押下カウンタ (1〜15 で循環) |
| `isButton` | このスキャンでボタンが押された=1 |
| `entranceCount` | 入室回数カウンタ (1〜3 で循環) |
| `goOutCount` | 外出回数カウンタ (1〜3 で循環) |
| `battery` | バッテリー残量 % ([注記](#バッテリーについて)) |
| `encrypted` | 1 なら暗号化されており、他の値は復号が必要 |

### サービスデータのバイト配置

**出典: SwitchBot 公式 BLE 仕様書**
[OpenWonderLabs/SwitchBotAPI-BLE](https://github.com/OpenWonderLabs/SwitchBotAPI-BLE)
→ `devicetypes/contactsensor.md`

| 位置 | 内容 |
|---|---|
| `[0]` bit7 | 暗号化の有無 |
| `[0]` bit[6:0] | デバイス種別 `'d'`(0x64)=常時アドバタイズ / `'D'`(0x44)=ペアリング |
| `[1]` bit7 | スコープテスト済みか |
| `[1]` bit6 | **PIR State** 1=誰か動いている |
| `[2]` bit[6:0] | バッテリー残量 % |
| `[3]` bit7 | PIR 経過秒の最上位ビット (×65536) |
| `[3]` bit6 | HAL 経過秒の最上位ビット (×65536) |
| `[3]` bit[1:2] | **Hal State** 0=閉 / 1=開 / 2=開いたままタイムアウト |
| `[3]` bit0 | Light Level 0=暗い / 1=明るい |
| `[4][5]` | 最後の PIR 検知からの経過秒 下位16bit (ビッグエンディアン) |
| `[6][7]` | 最後の HAL (ドア磁石) 検知からの経過秒 下位16bit (同上) |
| `[8]` bit[6:7] | 入室回数カウンタ (1〜3 循環) |
| `[8]` bit[4:5] | 外出回数カウンタ (1〜3 循環) |
| `[8]` bit[0:3] | ボタン押下カウンタ (1〜15 循環) |

#### ドア状態は 2bit の値です

`[3]` の bit1 と bit2 は**独立したフラグではなく、2bit でひとつの値**です。

| 値 | 意味 | `isOpen` | `isLeaveOpen` |
|---|---|---|---|
| 0 | 閉 | 0 | 0 |
| 1 | 開 | 1 | 0 |
| 2 | 開いたままタイムアウト | **1** | 1 |

bit1 を「開」、bit2 を「開けっ放し」として独立に読むと、値が 2 のとき
**ドアは開いているのに `isOpen=0`（閉）と誤判定します**。ネット上のサンプルには
この読み方をしているものがあるので注意してください。

#### 経過秒は 17bit です

`[4][5]` と `[6][7]` は 16bit ですが、**最上位ビットが `[3]` の bit7 / bit6 に
あります**。これを無視すると 65535 秒 (約18時間) で値が一周します。

#### バッテリーについて

公式仕様では `[2]` の下位7bit がバッテリー残量です。**人感センサーでは実機で正しく
取得できています** (19% を確認)。一方 **開閉センサー (W1201500) の手元の個体は
常に 0 を返します**。解析の誤りではなく個体・ファームウェア側の挙動です。確実に取得するには BLE 接続して
「0x02 Get device basic information」コマンドを投げる必要があり、その応答の
Byte 0 がバッテリー残量です。本リポジトリのスクリプトは接続しない (アドバタイズを
受信するだけ) ため、現状は 0 のままです。

## 人感センサー

| キー | 意味 |
|---|---|
| `isMotion` | 動きを検知=1 / 検知なし=0 |
| `secSincePir` | 最後の検知からの経過秒 |
| `isIlluminance` | 明るい=1 / 暗い=0 (予約値のときは `None`) |
| `lightLevel` | 生の値。1=暗い / 2=明るい (0 と 3 は予約) |
| `sensingDistance` | 検知距離 0=長 / 1=中 / 2=短 |
| `ledEnabled` | 本体 LED 設定 |
| `iotEnabled` | クラウド連携設定 |
| `battery` | バッテリー残量 % (実機で取得確認済み) |
| `isON` | `isMotion` の旧名 (互換用) |

### サービスデータのバイト配置 (人感センサー)

**出典:** [OpenWonderLabs/SwitchBotAPI-BLE](https://github.com/OpenWonderLabs/SwitchBotAPI-BLE)
→ `devicetypes/motionsensor.md`

| 位置 | 内容 |
|---|---|
| `[0]` bit[6:0] | デバイス種別 `'s'`(0x73)=常時アドバタイズ / `'S'`(0x53)=ペアリング |
| `[1]` bit7 | スコープテスト済みか |
| `[1]` bit6 | **PIR State** 1=誰か動いている |
| `[2]` bit[6:0] | バッテリー残量 % |
| `[3][4]` | 最後の PIR 検知からの経過秒 下位16bit (`[3]`=上位8bit `[4]`=下位8bit) |
| `[5]` bit7 | 同経過秒の**最上位ビット** (×65536) |
| `[5]` bit6 | 予約 |
| `[5]` bit5 | LED 設定 0=無効 / 1=有効 |
| `[5]` bit4 | IoT 設定 0=無効 / 1=有効 |
| `[5]` bit[3:2] | 検知距離 00=長 / 01=中 / 10=短 |
| `[5]` bit[1:0] | 明るさ 01=暗い / 10=明るい (00 と 11 は予約) |

#### 経過秒は 17bit です

開閉センサーと同じく、経過秒の最上位ビットが**別のバイト (`[5]` bit7) に
あります**。ネット上のサンプルには `[4]` だけを読んでいるものがありますが、
それでは **255秒 (約4分) で値が一周**し、それ以上前の検知と区別できません。

```
secSincePir = ([5] bit7 << 16) | ([3] << 8) | [4]
```

#### 明るさは 2bit の値です

`00` と `11` は予約値です。`(値 & 0b11) - 1` のように引き算で 0/1 に変換すると、
予約値のときに `-1` や `2` が出ます。本実装は予約値を `None` として扱います。

### ボタン押下の検出

センサー側は押下回数を 1→2→…→15→1 と循環するカウンタで持っています。
前回値との差分 `(count - prev) % 15` が 0 以外なら「押された」と判定します。

## ライセンス

MIT
