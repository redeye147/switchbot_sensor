# SwitchBot 開閉センサー — Raspberry Pi セットアップ手順

## 0. 前提
- Raspberry Pi 3 / 4 / 5 / Zero 2 W など **BLE 内蔵モデル**
- Raspberry Pi OS (Bookworm 以降推奨)
- SwitchBot アプリでセンサーの初期設定を済ませておくこと
- **クラウドサービス連携は OFF でも動作します**（BLE を直接受信するため）

## 1. Bluetooth の確認
```bash
sudo systemctl status bluetooth      # active (running) であること
bluetoothctl -- show                 # Powered: yes であること
hciconfig                            # hci0 が UP RUNNING であること
```
`DOWN` の場合: `sudo hciconfig hci0 up`

## 2. 依存パッケージ
```bash
sudo apt update
sudo apt install -y python3-venv python3-pip bluez
```

## 3. 仮想環境を作って bleak を入れる
Bookworm 以降は PEP 668 により `pip install` が直接できません。また Bullseye では
システムの `/usr/lib/python3/dist-packages` が venv に漏れて古いパッケージを掴む
ことがあります。同梱の `setup.sh` が両方を処理します。
```bash
cd ~/switchbot_sensor
./setup.sh
```
最後に `OK: システムの dist-packages は遮断されています` と出れば成功です。

手動で作る場合は、PYTHONPATH を外し `include-system-site-packages` が `false` で
あることを確認してください。
```bash
env -u PYTHONPATH python3 -m venv venv
grep include-system-site-packages venv/pyvenv.cfg   # false であること
env -u PYTHONPATH ./venv/bin/pip install -r requirements.txt
```

## 4. MAC アドレスを調べる
```bash
cd ~/switchbot_sensor
./venv/bin/python switchbot_contact.py --scan
```
`type=0x64 (d)  <= 開閉センサー` と出た行の MAC が対象です。
（`sudo hcitool lescan` でも一覧できますが、bluez の新しい版では非推奨です）

## 5. 動作確認
```bash
./venv/bin/python switchbot_contact.py --mac <調べたMAC>
```
ドアを開け閉め／ボタンを押して値が変わることを確認します。

## 6. 自動起動 (systemd)
```bash
sudo cp systemd/switchbot-contact.service /etc/systemd/system/
sudo nano /etc/systemd/system/switchbot-contact.service   # User / パス / MAC を自分の環境に合わせる
sudo systemctl daemon-reload
sudo systemctl enable --now switchbot-contact
```
ログの確認:
```bash
journalctl -u switchbot-contact -f
```

---

## bluepy 版を使う場合（元コードの構成のまま）
bluepy は 2019 年で更新が止まっており、新しい OS ではビルドに失敗することがあります。
それでも使う場合:
```bash
sudo apt install -y libglib2.0-dev
./venv/bin/pip install bluepy
```
bluepy は raw HCI ソケットを使うため **root 権限が必要**です。sudo 常用を避けるには
ヘルパーバイナリに capability を付けます:
```bash
HELPER=$(./venv/bin/python -c "import bluepy,os;print(os.path.join(os.path.dirname(bluepy.__file__),'bluepy-helper'))")
sudo setcap 'cap_net_raw,cap_net_admin+eip' "$HELPER"
```

---

## つまずきやすいポイント
| 症状 | 原因・対処 |
|---|---|
| 何も表示されない | MAC が**大文字**。bluepy / このスクリプトは小文字比較なので小文字にする |
| `Failed to execute management command 'le on'` | bluepy を sudo なしで実行している。上の setcap を実施 |
| `Permission denied (bluepy-helper)` | 同上 |
| bluepy の pip install が失敗する | `libglib2.0-dev` 不足、または Python 3.11+ 非対応。bleak 版を使う |
| たまに値が取れない | BLE アドバタイズは取りこぼすもの。前回値を保持して使う設計にする |
| 出力が多すぎる | bleak 版は受信のたびに表示する。`print_state` の先頭で前回値と比較し、変化時のみ出力する |
| センサーが遠い | rssi が -90 を下回ると不安定。-70 前後を目安に設置する |
| `ImportError: cannot import name 'Buffer' from 'typing_extensions'` | システムの古い typing_extensions が venv より優先されている。`./setup.sh` で venv を作り直す |
| pip が `Not uninstalling ... outside environment` と言う | 同上。venv に dist-packages が漏れているサイン |
| `-bash: 予期しないトークン \`newline' 周辺に構文エラー` | `--mac <MAC>` の山括弧をそのまま貼り付けている。`<` はリダイレクト記号なので外して `--mac c4:88:9c:aa:ab:2f` と書く |
