#!/usr/bin/env bash
# お風呂タイマーが反応しないときの状況を一度に集める。
#
#   ./diagnose.sh            # 出力をそのまま貼れる形で表示
#
# BLE の受信確認を含むため 20 秒ほどかかる。
set -u
cd "$(dirname "$0")"

MAC="${1:-c4:88:9c:aa:ab:2f}"
SERVICE=bath-timer
UNIT=/etc/systemd/system/$SERVICE.service

hr() { printf '\n===== %s =====\n' "$1"; }

hr "1. リポジトリの版"
git log --oneline -1 2>/dev/null || echo "git リポジトリではありません"
echo "未コミットの変更:"
git status --porcelain 2>/dev/null | head -5 || true

hr "2. 配置済みのユニットが最新か"
if [ -f "$UNIT" ]; then
    if diff -q systemd/$SERVICE.service "$UNIT" >/dev/null 2>&1; then
        echo "一致しています (最新の修正が反映済み)"
    else
        echo "!! 配置済みのユニットがリポジトリと違います。以下で更新してください:"
        echo "   sudo cp systemd/$SERVICE.service $UNIT"
        echo "   sudo systemctl daemon-reload && sudo systemctl restart $SERVICE"
        echo "--- 差分 ---"
        diff systemd/$SERVICE.service "$UNIT" | head -20
    fi
else
    echo "!! $UNIT がありません (サービス未導入)"
fi

hr "3. サービスの状態"
systemctl is-enabled $SERVICE 2>&1 | sed 's/^/enabled: /'
systemctl is-active $SERVICE 2>&1 | sed 's/^/active : /'
systemctl show $SERVICE -p ExecStart --value 2>/dev/null | sed 's/^/exec   : /'
echo "起動からの経過:"
systemctl show $SERVICE -p ActiveEnterTimestamp --value 2>/dev/null

hr "4. 二重起動していないか"
pgrep -af bath_timer.py || echo "プロセスなし"

hr "5. Bluetooth アダプタ"
bluetoothctl show 2>/dev/null | grep -E "Powered|Discovering" || echo "bluetoothctl が使えません"

hr "6. 直近のログ (今回の起動分)"
journalctl -u $SERVICE -b --no-pager | tail -30

hr "7. 前回の起動分の末尾 (再起動前に何があったか)"
journalctl -u $SERVICE -b -1 --no-pager 2>/dev/null | tail -10 || echo "前回分のログがありません"

hr "8. センサーを実際に受信できるか (20秒)"
echo "対象: $MAC"
env -u PYTHONPATH ./venv/bin/python - "$MAC" <<'PY'
import asyncio, sys, time
sys.path.insert(0, ".")
from sitefix import strip_dist_packages
strip_dist_packages()
from bleak import BleakScanner
from switchbot_protocol import parse_contact

mac = sys.argv[1].lower()
seen = []

def cb(device, adv):
    if device.address.lower() != mac:
        return
    for payload in adv.service_data.values():
        data = parse_contact(payload)
        if data:
            seen.append((time.strftime("%H:%M:%S"), adv.rssi, data))

async def main():
    async with BleakScanner(cb):
        await asyncio.sleep(20)

asyncio.run(main())

if not seen:
    print("!! 20秒間、一度も受信できませんでした")
    print("   考えられる原因: 電波が届かない / 電池切れ / MAC が違う")
    print("   周囲の SwitchBot 機器は以下で確認できます:")
    print("     ./venv/bin/python switchbot_contact.py --scan")
else:
    ts, rssi, d = seen[-1]
    rssis = [r for _, r, _ in seen]
    print(f"受信できています: {len(seen)}パケット")
    print(f"  rssi        : 最新 {rssi} / 最小 {min(rssis)} / 最大 {max(rssis)}")
    if max(rssis) < -85:
        print("  !! 電波がかなり弱いです (-85 を下回っています)。押下を取りこぼします")
    print(f"  ドア        : {d['doorState']} (0=閉 1=開 2=開けっ放し)")
    print(f"  ボタン回数  : {d['buttonCount']}")
    print(f"  最後の開閉  : {d['secSinceHal']}秒前")
    print("  この状態でボタンを押し、ボタン回数が変わるか確認してください:")
    print(f"    ./venv/bin/python switchbot_contact.py --mac {mac}")
PY

hr "おわり"
echo "この出力をそのまま貼ってください。"
