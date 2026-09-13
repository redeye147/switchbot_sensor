#!/usr/bin/env python3
# coding: utf-8
"""システムの dist-packages を import パスから取り除く。

Raspberry Pi OS では PYTHONPATH に /usr/lib/python3/dist-packages が入っている
ことがあり、これは venv の site-packages より優先されます。その結果 venv に
新しい版を入れても、システム側の古い版 (例: typing_extensions 3.7.4.3) を
掴んでしまいます。bleak は typing_extensions.Buffer (4.6 以降) を使うため、
これが起きると `ImportError: cannot import name 'Buffer'` で落ちます。

エントリポイントの先頭で、サードパーティを import する前に呼んでください。
venv の中で動かす前提なので、システムの dist-packages は不要です。
"""
import sys

DIST_PACKAGES = "dist-packages"


def strip_dist_packages():
    """sys.path から dist-packages を除去し、除去したパスを返す。"""
    removed = [p for p in sys.path if DIST_PACKAGES in p]
    if removed:
        sys.path[:] = [p for p in sys.path if DIST_PACKAGES not in p]
        # 既に古い版が読み込み済みなら捨てて、次の import をやり直させる
        for name, mod in list(sys.modules.items()):
            origin = getattr(mod, "__file__", None) or ""
            if DIST_PACKAGES in origin:
                del sys.modules[name]
    return removed


def in_venv():
    return sys.prefix != sys.base_prefix
