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

| キー | 意味 | 検証状況 |
|---|---|---|
| `isOpen` | 開=1 / 閉=0 | 実機確認済み |
| `buttonCount` | ボタン押下カウンタ (1〜15 で循環) | 実機確認済み |
| `isButton` | このスキャンでボタンが押された=1 | 実機確認済み |
| `secSinceChange` | 最後の開閉からの経過秒。開にも閉にもリセット | 実機確認済み |
| `secSinceButton` | 最後のボタン押下からの経過秒 | 実機確認済み |
| `time` | `secSinceChange` の旧名 (互換用) | — |
| `isLeaveOpen` | 開けっ放し=1 | 未検証 |
| `isIlluminance` | 明るい=1 / 暗い=0 | 未検証 (実機では常に 0) |
| `battery` | 常に `None` | **サービスデータに存在しません** |

### サービスデータのバイト配置

BLE の AD Type `0x16` (Service Data)、**サービス UUID `0xFD3D`** のペイロードです。

| 位置 | 内容 |
|---|---|
| `[0] & 0x7F` | デバイス種別 (開閉センサー = `0x64` = `'d'`) |
| `[1]` | フラグ。ボタン押下で `0x20` → `0x60` と変化。用途未特定 |
| `[2]` | 観測範囲では常に 0 |
| `[3]` bit0 | 照度 (未検証) |
| `[3]` bit1 | 開閉状態 |
| `[3]` bit2 | 開けっ放しフラグ |
| `[4][5]` | 最後のボタン押下からの経過秒 (**16bit ビッグエンディアン**) |
| `[6][7]` | 最後の開閉からの経過秒 (**16bit ビッグエンディアン**) |
| `[8]` bit7 | ドアを開けるとクリアされる。用途未特定 |
| `[8] & 0x0F` | ボタン押下カウンタ |

> このバイト配置は**公式仕様書ではなく、実機 (W1201500) の観測から導いたもの**です。
> 2回の独立したキャプチャで辻褄が合うことを確認していますが、ファームウェア版に
> よって異なる可能性があります。自分の個体を確認するには `--raw` を使ってください。
>
> ```bash
> ./venv/bin/python switchbot_contact.py --mac <MAC> --raw
> ```

#### 元のスクリプトとの違い

ネット上でよく見る bluepy 版のサンプルは、サービス UUID `0x000D` の**古い形式**を
前提にしています。手元の個体は `0xFD3D` で広告しており、次の点が異なりました。

| 項目 | 古い形式の想定 | 実機 (fd3d) |
|---|---|---|
| バッテリー | `[2] & 0x7F` | **存在しない** (`[2]` は常に 0) |
| 経過時間 | `[7]` の 1バイト | `[6][7]` の 16bit。しかも「開けっ放し時間」ではなく「最後の開閉からの経過秒」 |
| `[4][5]` | 未使用 | 最後のボタン押下からの経過秒 |

### ボタン押下の検出

センサー側は押下回数を 1→2→…→15→1 と循環するカウンタで持っています。
前回値との差分 `(count - prev) % 15` が 0 以外なら「押された」と判定します。

## ライセンス

MIT
