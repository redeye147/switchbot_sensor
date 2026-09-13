#!/usr/bin/env bash
# クリーンな venv を作って依存を入れる。
#
# Raspberry Pi OS (特に Bullseye) では /usr/lib/python3/dist-packages が venv の
# import パスに混入し、システム側の古いパッケージが venv 側より優先されることが
# あります (例: typing_extensions 3.7.4.3 が 4.x を隠して bleak が ImportError)。
# このスクリプトはその経路を明示的に塞ぎます。
set -euo pipefail
cd "$(dirname "$0")"

VENV=venv

if [ -d "$VENV" ]; then
    echo "既存の $VENV を作り直します"
    rm -rf "$VENV"
fi

# PYTHONPATH を外した状態で venv を作る (継承されると dist-packages が混入する)
env -u PYTHONPATH python3 -m venv "$VENV"

# システムの dist-packages を確実に遮断する
if grep -q '^include-system-site-packages *= *true' "$VENV/pyvenv.cfg"; then
    echo "include-system-site-packages を false に修正します"
    sed -i 's/^include-system-site-packages *= *true/include-system-site-packages = false/' "$VENV/pyvenv.cfg"
fi

env -u PYTHONPATH "$VENV/bin/pip" install --upgrade pip
env -u PYTHONPATH "$VENV/bin/pip" install -r requirements.txt

echo
echo "=== 検証 ==="
env -u PYTHONPATH "$VENV/bin/python" verify_env.py

echo
echo "次のコマンドで MAC アドレスを調べてください:"
echo "  ./venv/bin/python switchbot_contact.py --scan"
