# switchbot_sensor

SwitchBot の BLE センサーを Raspberry Pi から受信するスクリプト集。

SwitchBot デバイスは BLE のアドバタイジングパケットにセンサー値を載せて周囲にばら撒いています。
本リポジトリのスクリプトはそれを**受信するだけ**で、ペアリングも接続もクラウド連携も不要です。

## 対応デバイス

| デバイス | 種別バイト | スクリプト |
|---|---|---|
| 開閉センサー (Contact Sensor) | `0x64` (`'d'`) | `switchbot_contact.py` |

## クイックスタート

```bash
sudo apt install -y python3-venv bluez
./setup.sh

# 周囲の SwitchBot を探して MAC アドレスを調べる
./venv/bin/python switchbot_contact.py --scan

# 受信開始
./venv/bin/python switchbot_contact.py --mac c4:88:9c:aa:ab:2f
```

詳細なセットアップと自動起動の設定は [docs/SETUP.md](docs/SETUP.md) を参照。

## ファイル構成

| パス | 内容 |
|---|---|
| `switchbot_contact.py` | **推奨実装**。bleak (BlueZ D-Bus) 版。root 権限不要 |
| `switchbot_contact_bluepy.py` | bluepy 版。旧実装互換。root 権限または setcap が必要 |
| `systemd/switchbot-contact.service` | 自動起動用 systemd ユニット |
| `setup.sh` | クリーンな venv を作成。dist-packages の混入を遮断する |
| `verify_env.py` | venv がシステムから隔離されているかを診断する |
| `contact_protocol.py` | サービスデータの解析。bleak 版と bluepy 版で共有 |
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

公式仕様では `[2]` の下位7bit がバッテリー残量ですが、**手元の個体 (W1201500) は
常に 0 を返します**。確実に取得するには BLE 接続して
「0x02 Get device basic information」コマンドを投げる必要があり、その応答の
Byte 0 がバッテリー残量です。本リポジトリのスクリプトは接続しない (アドバタイズを
受信するだけ) ため、現状は 0 のままです。

### ボタン押下の検出

センサー側は押下回数を 1→2→…→15→1 と循環するカウンタで持っています。
前回値との差分 `(count - prev) % 15` が 0 以外なら「押された」と判定します。

## ライセンス

MIT
