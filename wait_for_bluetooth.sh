#!/usr/bin/env bash
# Bluetooth アダプタが使えるようになるまで待つ。
#
# systemd の After=bluetooth.target は「Bluetooth の初期化を始めた」程度の保証しか
# なく、起動直後はアダプタがまだ電源オンになっていないことがある。その状態で
# スキャンを始めると、例外も出ないまま何も受信しない。
#
# 待てなかった場合も 0 で終了する。サービス側に受信途絶の監視があるため、
# ここで起動を止めるより、起動してから復帰させたほうがよい。
set -u
TIMEOUT="${1:-60}"

for _ in $(seq "$TIMEOUT"); do
    if bluetoothctl show 2>/dev/null | grep -q "Powered: yes"; then
        echo "Bluetooth アダプタは使用可能です"
        exit 0
    fi
    sleep 1
done

echo "Bluetooth アダプタが ${TIMEOUT} 秒以内に使用可能になりませんでした。" \
     "そのまま起動します (受信途絶は監視して自動復帰します)"
exit 0
