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
| `sitefix.py` | 実行時にシステムの dist-packages を import パスから除外する |
| `tests/test_offline.py` | BLE ハードウェアなしで解析ロジックを検証する |
| `docs/SETUP.md` | ラズパイ セットアップ手順とトラブルシューティング |

## 取得できる値

| キー | 意味 |
|---|---|
| `isIlluminance` | 明るい=1 / 暗い=0 |
| `isOpen` | 開=1 / 閉=0 |
| `isLeaveOpen` | 開けっ放し=1 |
| `time` | 最後の状態変化からの経過秒。1バイトなので 255 で頭打ち |
| `buttonCount` | ボタン押下カウンタ (1〜15 で循環) |
| `isButton` | このスキャンでボタンが押された=1 |
| `battery` | バッテリー残量 % — **未検証**。実機で常に 0 を返すため調査中 |

### サービスデータのバイト配置

BLE の AD Type `0x16` (Service Data) のペイロードを次のように解釈しています。

| 位置 | 内容 |
|---|---|
| `[0] & 0x7F` | デバイス種別 (開閉センサー = `0x64`) |
| `[2] & 0x7F` | バッテリー残量 % — **未検証**、実機では常に 0 |
| `[3]` bit0 | 照度 |
| `[3]` bit1 | 開閉状態 |
| `[3]` bit2 | 開けっ放しフラグ |
| `[7]` | 最後の状態変化からの経過秒 (上位バイトは未特定) |
| `[8] & 0x0F` | ボタン押下カウンタ |

> **注意**: このバイト配置は公開されている実装に基づく推定です。実機 (開閉センサー
> W1201500) での確認状況は次のとおりです。
>
> - `isOpen` / `buttonCount` / `isButton` — **確認済み**
> - `time` — 動作するが意味が異なる。「開けっ放し時間」ではなく「最後の状態変化からの
>   経過秒」で、1バイトのため 255 で頭打ちになる。上位バイトの位置は未特定
> - `battery` — **誤り**。実機で常に 0 を返すため `[2]` はバッテリーではない
> - `isIlluminance` — 未確認
>
> バイト配置を自分で確かめるには `--raw` を使ってください。
>
> ```bash
> ./venv/bin/python switchbot_contact.py --mac <MAC> --raw
> ```

### ボタン押下の検出

センサー側は押下回数を 1→2→…→15→1 と循環するカウンタで持っています。
前回値との差分 `(count - prev) % 15` が 0 以外なら「押された」と判定します。

## ライセンス

MIT
