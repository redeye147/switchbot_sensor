# switchbot_sensor

SwitchBot の BLE センサーを Raspberry Pi から受信するスクリプト集。

SwitchBot デバイスは BLE のアドバタイジングパケットにセンサー値を載せて周囲にばら撒いています。
本リポジトリのスクリプトはそれを**受信するだけ**で、ペアリングも接続もクラウド連携も不要です。

## 対応デバイス

| デバイス | 種別バイト | スクリプト |
|---|---|---|
| 開閉センサー (Contact Sensor) | `0x64` (`'d'`) | `switchbot_contact.py` |
| 人感センサー (Motion Sensor) | `0x73` (`'s'`) | `switchbot_motion.py` |
| カーテン (Curtain / Curtain2) | `0x63` (`'c'`) | `switchbot_curtain.py` |
| プラグミニ (Plug Mini) | `0x67` (`'g'`) ※`0x6A` も暫定対応 | `switchbot_plug.py` |
| スマート電球 (Color Bulb) | `0x75` (`'u'`) ※実機未検証 | `switchbot_light.py` |
| テープライト (LED Strip Light) | `0x72` (`'r'`) ※実機未検証 | `switchbot_light.py` |

## クイックスタート

```bash
sudo apt install -y python3-venv bluez
./setup.sh

# 周囲の SwitchBot を探して MAC アドレスを調べる
./venv/bin/python switchbot_contact.py --scan

# 受信開始
./venv/bin/python switchbot_contact.py --mac c4:88:9c:aa:ab:2f   # 開閉センサー
./venv/bin/python switchbot_motion.py  --mac d2:dc:22:fd:6d:d1   # 人感センサー
./venv/bin/python switchbot_curtain.py --mac dc:87:13:08:75:83   # カーテン
./venv/bin/python switchbot_plug.py    --watch                   # プラグミニ全台
./venv/bin/python switchbot_light.py   --scan                    # ランプを探す
```

詳細なセットアップと自動起動の設定は [docs/SETUP.md](docs/SETUP.md) を参照。

## ファイル構成

| パス | 内容 |
|---|---|
| `switchbot_contact.py` | **推奨実装**。bleak (BlueZ D-Bus) 版。root 権限不要 |
| `switchbot_motion.py` | 人感センサー用。bleak 版 |
| `switchbot_curtain.py` | カーテン用。bleak 版 |
| `switchbot_plug.py` | プラグミニ用。複数台を同時監視できる |
| `switchbot_light.py` | スマート電球 / テープライト用。実機未検証 |
| `switchbot_control.py` | **電球を BLE 接続して制御する**（唯一の送信側） |
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

## カーテン

**Curtain / Curtain2 (種別 `'c'`) のみ対応しています。** Curtain 3 は種別バイトも
バイト配置も異なる (`devicetypes/curtain3.md`) ため解析しません。

受信専用です。開閉の操作はできません。

| キー | 意味 |
|---|---|
| `position` | 現在位置 % |
| `isMoving` | 動作中=1 / 停止=0 |
| `lightLevel` | 明るさレベル (1〜10) |
| `calibrated` | キャリブレーション済み=1 (0 なら要調整) |
| `connectable` | 接続を許可しているか |
| `deviceChain` | デバイスチェーン数 |
| `battery` | バッテリー残量 % |

### サービスデータのバイト配置 (カーテン)

**出典:** [OpenWonderLabs/SwitchBotAPI-BLE](https://github.com/OpenWonderLabs/SwitchBotAPI-BLE)
→ `devicetypes/curtain.md`

| 位置 | 内容 |
|---|---|
| `[0]` bit[6:0] | デバイス種別 `'c'`(0x63)=常時アドバタイズ / `'C'`(0x43)=ペアリング |
| `[1]` bit7 | 接続を許可しているか |
| `[1]` bit6 | キャリブレーション済みか |
| `[2]` bit[6:0] | バッテリー残量 % |
| `[3]` bit7 | 動作状態 0=停止 / 1=動作中 |
| `[3]` bit[6:0] | 現在位置 % |
| `[4]` bit[7:4] | 明るさレベル (1〜10) |
| `[4]` bit[3:0] | デバイスチェーン数 |

#### 位置の向きは仕様書に書かれていません

`0` が全開なのか全閉なのかは公式仕様に明記がありません。一般には
**0=全開 / 100=全閉** とされますが、**実機でカーテンを動かして確認してください**。

#### 動作ビットは位置に混ざります

`[3]` は bit7 が動作状態、bit[6:0] が位置です。バイトをそのまま位置として読むと、
**動作中に位置が +128 された値**になります (例: 位置30% が 158 と表示される)。

## プラグミニ

プラグミニは Wi-Fi 機器ですが、状態を **BLE でもアドバタイズ**しています。本リポジトリ
はそれを受信するだけなので、クラウド連携もトークンも不要です。**ON/OFF の操作は
できません** (受信専用)。

### 他機種と構造が違います

センサー類は **サービスデータ** に値を載せますが、**プラグミニは
メーカー固有データ (company ID `0x0969`) に載せます**。同じ `--raw` で両方
ダンプできますが、解析対象のバイト列が別物である点に注意してください。

```bash
./venv/bin/python switchbot_plug.py --scan     # 周囲のプラグミニを一覧
./venv/bin/python switchbot_plug.py --watch    # 見つかった全台をまとめて監視
./venv/bin/python switchbot_plug.py --mac AA:.. --mac BB:..   # 台を指定
```

| キー | 意味 |
|---|---|
| `isOn` | 電源 ON=1 / OFF=0 |
| `powerW` | 消費電力 W ([注記](#消費電力の単位)) |
| `powerRaw` | 消費電力の生値 |
| `isOverload` | 過負荷 (15A 超)=1 |
| `hasTimer` / `hasDelay` | タイマー / ディレイ設定の有無 |
| `utcSynced` | UTC 時刻が同期済みか |
| `wifiRssiRaw` | `[9]` の生値 ([注記](#9-は機種により意味が違います)) |
| `wifiRssiDbm` | dBm と解釈できる場合のみ。できなければ `None` |
| `sequence` | シーケンス番号 1〜255 (更新のたびに増加) |
| `mac` | ペイロードに入っている MAC アドレス |

### メーカー固有データのバイト配置 (プラグミニ)

**出典:** [OpenWonderLabs/SwitchBotAPI-BLE](https://github.com/OpenWonderLabs/SwitchBotAPI-BLE)
→ `devicetypes/plugmini.md`

仕様書は company ID を含む生の AD ペイロードで位置を数えていますが、bleak の
`adv.manufacturer_data[0x0969]` は **company ID の 2 バイトを除いた中身**を返します。
下表はその前提の位置です (仕様書の Byte 番号 − 2)。

| 位置 | 内容 |
|---|---|
| `[0]`〜`[5]` | デバイスの MAC アドレス (ビッグエンディアン) |
| `[6]` | シーケンス番号 1〜255 |
| `[7]` | 電源状態 `0x00`=OFF / `0x80`=ON |
| `[8]` bit0 / bit1 / bit2 | ディレイ / タイマー / UTC 同期 |
| `[9]` | 接続中の Wi-Fi の RSSI ([注記](#9-は機種により意味が違います)) |
| `[10]` bit7 | **過負荷** (15A 超) |
| `[10]` bit[6:0] + `[11]` | **消費電力** (15bit) |

#### 消費電力の単位

単位は公式仕様に**明記がありません**。一般に **0.1W 単位**とされるため `powerW` を
併記していますが、**既知の負荷 (白熱電球やドライヤーなど W 数の分かるもの) を
つないで確認してください**。生値は `powerRaw` で確認できます。

#### 機種の判別はサービスデータで行います

SwitchBot は **全機種が** company ID `0x0969` のメーカー固有データを出しており、
しかも**先頭6バイトは一様に自分の MAC アドレス**です。そのため「長さが十分」
「MAC が一致する」だけでは機種を判別できず、他機種のデータをプラグミニとして
誤読します (開閉センサーが「1302 W」と表示される、など)。

判別は**サービスデータの機種バイト**で行ってください
(`switchbot_protocol.service_device_type()`)。`parse_plug()` 自体は与えられたバイト列を解析するだけなので、機種の判別を
省くと他機種のデータを読んでしまいます。

#### `[9]` は機種により意味が違います

仕様書は `[9]` を Wi-Fi の RSSI としていますが、**実機 (種別 `0x6A`) は 45〜48 と
いう正の値**を返します。RSSI は 0 以下にしかならないため、この機種では別の意味
(0〜100 の信号強度など) と思われます。

そのため生値を `wifiRssiRaw` に、dBm と解釈できる場合のみ `wifiRssiDbm` に入れ、
解釈できなければ `None` とします。**この値を根拠に「プラグミニかどうか」を判定
しないでください** (本物のプラグミニを弾いてしまいます)。

#### `0x6A` は公式一覧にありません

公式の機種一覧 (SwitchBotAPI-BLE の README) に載っているプラグミニは
**`g` (0x67) のみ**です。実機で観測される `j` (0x6A) を JP 版として受け付けています。**公式の裏付けは
ありませんが**、実機の生データは `[9]` を除いて `plugmini.md` の配置どおりで、
電源状態・シーケンス番号・消費電力がすべて整合することを確認済みです。

実機のサービスデータは `6a 00 64` の **3バイトしかありません**。他機種のような
センサー値は入っておらず、機種の判別にのみ使えます。

#### 過負荷ビットは消費電力に混ざります

`[10]` は bit7 が過負荷、bit[6:0] が電力の上位ビットです。`[10][11]` を素直に
16bit として読むと、**過負荷時に電力が +32768 された値**になります。

#### 表示が多くなりすぎないように

消費電力は常に微動するため、既定では **1.0W 以上動いたとき**か電源状態などが
変わったときだけ表示します。毎パケット見るには `--all` を付けてください。

## スマート電球 / テープライト

**実機で未検証です。** 公式仕様書どおりに実装してありますが、手元に機器が無いため
動作確認が取れていません。値がおかしい場合は `--raw` の出力からバイト配置を
見直してください。

プラグミニと同じく **メーカー固有データ** に状態が入ります。受信専用で、点灯/消灯
や色の変更はできません。

```bash
./venv/bin/python switchbot_light.py --scan      # 周囲のランプを探す
./venv/bin/python switchbot_light.py --watch     # 見つかった全台を監視
./venv/bin/python switchbot_light.py --devices   # ランプ以外も含めた SwitchBot 機器一覧
```

### 共通の値

| キー | 意味 |
|---|---|
| `isOn` | 点灯=1 / 消灯=0 |
| `brightness` | 明るさ 1〜100% |
| `networkStatus` | 0=Wi-Fi接続中 / 1=IoT接続中 / 2=IoT接続済み |
| `hasDelay` | ディレイ設定の有無 |
| `sequence` | シーケンス番号 1〜255 |

### スマート電球のみ

| キー | 意味 |
|---|---|
| `lightState` | 1=白色 / 2=カラー / 3=ダイナミック |
| `dynamicRate` | ダイナミックの速度 1〜100% |
| `rssiQualityBad` | RSSI 品質 1=不良 |
| `isPreset` | 点灯状態のプリセット有無 |
| `loopIndex` | ループ番号 |

### テープライトのみ

| キー | 意味 |
|---|---|
| `mode` | 2=カラー / 3=シーン / 4=ミュージック / 5=コントローラー |
| `colors` | `(R, G, B)` の一覧。最大8色、**各成分は 0〜3** |
| `faultCode` | 直近のフォールトコード (0=異常なし) |

### メーカー固有データのバイト配置

**出典:** [OpenWonderLabs/SwitchBotAPI-BLE](https://github.com/OpenWonderLabs/SwitchBotAPI-BLE)
→ `devicetypes/colorbulb.md` / `devicetypes/ledstriplight.md`

プラグミニと同様、下表は仕様書の Byte 番号から 2 を引いた位置です。

| 位置 | 電球 | テープライト |
|---|---|---|
| `[0]`〜`[5]` | MAC アドレス | 同左 |
| `[6]` | シーケンス番号 | 同左 |
| `[7]` bit7 / bit[6:0] | 電源 / 明るさ | 同左 |
| `[8]` bit7 / bit[6:4] | ディレイ / ネットワーク状態 | 同左 |
| `[8]` 下位 | bit3 プリセット, bit[2:0] 点灯モード | bit[3:0] 点灯モード |
| `[9]` | bit7 RSSI品質, bit[6:0] ダイナミック速度 | 色データの先頭 |
| `[9]`〜`[14]` | — | 色データ (2bit × 24) |
| `[10]` bit[7:2] | ループ番号 | — |
| `[15]` | — | フォールトコード |

#### テープライトの色は 2bit しかありません

`[9]`〜`[14]` の 6 バイトに `R0,G0,B0,R1,G1,B1,...` と **2bit ずつ 24 個**
詰まっています。つまり各成分は **0〜3 の 4 段階**で、8bit の RGB 値ではありません。

`R:G:B = 0:0:0` の色は「存在しない」を意味するため、`colors` から除いています
(実際の色数が 8 未満であることを表します)。

#### 電源ビットは明るさに混ざります

`[7]` は bit7 が電源、bit[6:0] が明るさです。カーテンの動作ビットやプラグミニの
過負荷ビットと同じ構造で、そのまま 1 バイトとして読むと**点灯中だけ明るさが
+128 された値**になります。

## 電球を制御する

ここまでのスクリプトはすべて**受信専用**ですが、`switchbot_control.py` だけは
**BLE 接続してコマンドを書き込みます**。クラウドもトークンも不要で、ラズパイから
直接、点灯/消灯・明るさ・色を変えられます。

```bash
./venv/bin/python switchbot_control.py --mac 80:65:99:9d:ad:de status
./venv/bin/python switchbot_control.py --mac 80:65:99:9d:ad:de on
./venv/bin/python switchbot_control.py --mac 80:65:99:9d:ad:de off
./venv/bin/python switchbot_control.py --mac 80:65:99:9d:ad:de toggle
./venv/bin/python switchbot_control.py --mac 80:65:99:9d:ad:de level 30
./venv/bin/python switchbot_control.py --mac 80:65:99:9d:ad:de rgb 0 0 255 --level 50
./venv/bin/python switchbot_control.py --mac 80:65:99:9d:ad:de cw 2700
```

`--dry-run` を付けると、接続せずに送信するバイト列だけ表示します。

```bash
$ ./venv/bin/python switchbot_control.py --mac ... --dry-run on
送信するパケット: 570f470101
```

### パケットの形式

**出典:** `devicetypes/colorbulb.md`
（`0x570F4701` 状態と色の設定 / `0x570F4801` 状態の読み取り）

```
57 0f 47 01 <サブコマンド> [引数...]
│  │  └──┴─ コマンド識別
│  └─ 拡張コマンド
└─ マジックナンバー
```

| サブコマンド | 動作 | 引数 |
|---|---|---|
| `0x01` / `0x02` / `0x03` | 点灯 / 消灯 / トグル | なし |
| `0x14` | 明るさ | Lvl (0〜100) |
| `0x16` | RGB | R, G, B |
| `0x12` | 明るさ + RGB | Lvl, R, G, B |
| `0x17` | 色温度 | C/W (2700〜6500K) |
| `0x13` | 明るさ + 色温度 | Lvl, C/W |

生成したパケットが仕様書の Example と 1 バイトも違わないことを
`tests/test_offline.py` で固定しています。

### 通信に使う characteristic

| 用途 | UUID |
|---|---|
| 書き込み (端末 → デバイス) | `cba20002-224d-11e6-9fb8-0002a5d5c51b` |
| 通知 (デバイス → 端末) | `cba20003-224d-11e6-9fb8-0002a5d5c51b` |

### 色温度は実機未検証です

色温度 (2700〜6500K) は 1 バイトに収まらず、仕様書の表がバイト位置を明示して
いません。2 バイトのビッグエンディアンとして送っていますが、**確認が取れて
いません**。効かない場合は `--dry-run` でバイト列を確認してください。

### 接続できない場合

- BLE 接続は**同時に 1 台**しか受け付けません。アプリが接続中だと失敗します
- ファームウェアによっては接続に暗号化・認証が必要な場合があります。その場合は
  応答のステータスが `0x07 デバイスが暗号化されている` になります
- 応答が返らない場合、コマンド自体は届いている可能性があります。電球の状態を
  `status` で確認してください

### ボタン押下の検出

センサー側は押下回数を 1→2→…→15→1 と循環するカウンタで持っています。
前回値との差分 `(count - prev) % 15` が 0 以外なら「押された」と判定します。

## ライセンス

MIT
