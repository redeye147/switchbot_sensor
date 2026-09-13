#!/usr/bin/env python3
# coding: utf-8
"""venv がシステムの dist-packages から隔離されているかを診断する。

Raspberry Pi OS では PYTHONPATH に /usr/lib/python3/dist-packages が設定されて
いることがあり、これは venv の site-packages より優先されます。その結果 venv に
新しい版を入れてもシステム側の古い版を掴み、bleak が
`ImportError: cannot import name 'Buffer' from 'typing_extensions'` で落ちます。

このスクリプトは問題を「隠さずに報告」します。実行時の回避は sitefix.py が
担当し、スキャナ側は自動で除外した上で動きます。
"""
import os
import sys
from importlib.metadata import version, PackageNotFoundError

DIST_PACKAGES = "dist-packages"
PACKAGES = ("typing_extensions", "bleak")


def ver(name):
    """typing_extensions は __version__ 属性を持たないためメタデータから取る。"""
    try:
        return version(name)
    except PackageNotFoundError:
        return "not installed"


def report_environment():
    print("python              :", sys.version.split()[0])
    print("sys.prefix          :", sys.prefix)
    print("venv                :", "yes" if sys.prefix != sys.base_prefix else "NO (venv の外です)")

    pythonpath = os.environ.get("PYTHONPATH", "")
    print("PYTHONPATH          :", pythonpath if pythonpath else "(未設定)")

    leaked_env = [p for p in pythonpath.split(os.pathsep) if DIST_PACKAGES in p]
    leaked_path = [p for p in sys.path if DIST_PACKAGES in p]
    return leaked_env, leaked_path


def report_imports():
    """素の状態で import できるかを見る。

    戻り値は (解決したパス, エラーメッセージ, 種別)。種別は "missing" (未インストール)
    か "shadowed" (システム側の古い版に隠されている)。
    """
    resolved = {}
    for name in PACKAGES:
        try:
            mod = __import__(name)
        except ModuleNotFoundError as e:
            return None, f"パッケージが入っていません: {e.name} -- ./setup.sh を実行してください", "missing"
        except ImportError as e:
            return None, f"{name} の import に失敗: {e}", "shadowed"
        resolved[name] = getattr(mod, "__file__", "?") or "?"
    return resolved, None, None


def main():
    leaked_env, leaked_path = report_environment()
    print()

    resolved, err, kind = report_imports()

    if resolved:
        for name, path in resolved.items():
            print(f"{name:20}: {ver(name)}")
            print("  ->", path)
        shadowed = [n for n, p in resolved.items() if DIST_PACKAGES in p]
    else:
        print("!!", err)
        # 未インストールと「古い版に隠されている」を混同しない
        shadowed = list(PACKAGES) if kind == "shadowed" else []

    if kind == "missing":
        # 依存が無い状態では import パスの診断をしても意味が薄い
        return 1

    print()
    problems = []
    if leaked_env:
        problems.append(
            "PYTHONPATH に dist-packages が含まれています: " + ", ".join(leaked_env))
    if leaked_path:
        problems.append(
            "sys.path に dist-packages が含まれています: " + ", ".join(leaked_path))
    if shadowed:
        problems.append(
            "システム側から読み込まれている: " + ", ".join(shadowed))

    if not problems:
        print("OK: システムの dist-packages は遮断されています")
        return 0

    for p in problems:
        print("!!", p)

    print()
    print("--- 対処 ---")
    print("スキャナ本体 (switchbot_contact.py) は sitefix.py が dist-packages を")
    print("自動で除外するため、この状態でも動作します。ただし恒久対応として")
    print("PYTHONPATH の設定を外すことをおすすめします:")
    print()
    print('  grep -rn PYTHONPATH ~/.bashrc ~/.profile /etc/profile.d/ 2>/dev/null')
    print()
    print("一時的に回避する場合:")
    print("  env -u PYTHONPATH ./venv/bin/python verify_env.py")
    return 1


if __name__ == "__main__":
    sys.exit(main())
