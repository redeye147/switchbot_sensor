#!/usr/bin/env python3
# coding: utf-8
"""venv がシステムの dist-packages から隔離されているかを検証する。

Raspberry Pi OS Bullseye ではシステムの typing_extensions 3.7.4.3 が venv 側の
4.x を隠してしまい、bleak が `ImportError: cannot import name 'Buffer'` で
落ちることがあります。その状態を検出します。
"""
import sys
from importlib.metadata import version, PackageNotFoundError


def ver(name):
    """typing_extensions は __version__ 属性を持たないためメタデータから取る。"""
    try:
        return version(name)
    except PackageNotFoundError:
        return "not installed"


def main():
    print("python              :", sys.version.split()[0])

    try:
        import typing_extensions
        import bleak
    except ModuleNotFoundError as e:
        print("パッケージが入っていません:", e.name)
        print()
        print("./setup.sh を実行して依存をインストールしてください。")
        return 1
    except ImportError as e:
        print("import に失敗しました:", e)
        print()
        print("パッケージは存在するのに名前が見つからない場合、システム側の古い版を")
        print("掴んでいます。./setup.sh を実行して venv を作り直してください。")
        return 1

    print("typing_extensions   :", ver("typing_extensions"))
    print("  ->", typing_extensions.__file__)
    print("bleak               :", ver("bleak"))
    print("  ->", bleak.__file__)

    problems = []

    leaked = [p for p in sys.path if "dist-packages" in p]
    if leaked:
        problems.append("sys.path にシステムの dist-packages が混入: " + ", ".join(leaked))

    for mod in (typing_extensions, bleak):
        if "dist-packages" in (mod.__file__ or ""):
            problems.append(f"{mod.__name__} がシステム側から読み込まれている")

    if problems:
        print()
        for p in problems:
            print("!!", p)
        print()
        print("./setup.sh を実行して venv を作り直してください。")
        return 1

    print()
    print("OK: システムの dist-packages は遮断されています")
    return 0


if __name__ == "__main__":
    sys.exit(main())
