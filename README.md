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
| `time` | 開けっ放しの経過秒数 |
| `buttonCount` | ボタン押下カウンタ (1〜15 で循環) |
| `isButton` | このスキャンでボタンが押された=1 |
| `battery` | バッテリー残量 % |

### サービスデータのバイト配置

BLE の AD Type `0x16` (Service Data) のペイロードを次のように解釈しています。

| 位置 | 内容 |
|---|---|
| `[0] & 0x7F` | デバイス種別 (開閉センサー = `0x64`) |
| `[2] & 0x7F` | バッテリー残量 % |
| `[3]` bit0 | 照度 |
| `[3]` bit1 | 開閉状態 |
| `[3]` bit2 | 開けっ放しフラグ |
| `[7]` | 開けっ放し経過秒 |
| `[8] & 0x0F` | ボタン押下カウンタ |

> **注意**: このバイト配置は公開されている実装に基づくもので、ファームウェア版によって
> 変わる可能性があります。特に `time` は長時間の値が仕様どおりかを実機で確認してください。

### ボタン押下の検出

センサー側は押下回数を 1→2→…→15→1 と循環するカウンタで持っています。
前回値との差分 `(count - prev) % 15` が 0 以外なら「押された」と判定します。

## ライセンス

MIT
