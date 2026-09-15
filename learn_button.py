#!/usr/bin/env python3
# coding: utf-8
"""どの SwitchBot 機器がボタンなのかを、押した時刻との対応から特定する。

「アドバタイズが変化したら押下」では特定できない。開閉センサーや人感センサーは
経過秒カウンタを持っており、ボタンと無関係に数秒おきに変化し続けるためである。

そこで、押していない時間帯の変化 (ノイズ) をまず測り、そのうえで「今押して
ください」と指示した数秒間に変化した機器を記録する。毎回の押下で必ず変化し、
かつ普段は静かな機器が、探しているボタンである。

    ./venv/bin/python learn_button.py
"""
import time

# 押下ラウンドの既定値
BASELINE_SECONDS = 12.0
ROUNDS = 3
WINDOW_SECONDS = 4.0
GAP_SECONDS = 6.0


class Recorder:
    """機器ごとのアドバタイズ変化を時刻付きで記録する。"""

    def __init__(self):
        self.last = {}                  # addr -> payload
        self.changes = []               # (時刻, addr) 変化した瞬間
        self.first_seen = {}            # addr -> 時刻
        self.types = {}                 # addr -> 機種バイト

    def record(self, addr, dtype, payload, now):
        if addr not in self.first_seen:
            self.first_seen[addr] = now
            self.types[addr] = dtype
            self.last[addr] = payload
            return
        if self.last[addr] != payload:
            self.changes.append((now, addr))
            self.last[addr] = payload


def analyse(rec, baseline_span, windows):
    """押下ウィンドウとの対応から候補を順位付けする。

    baseline_span: (開始, 終了) 押していない時間帯
    windows:       [(開始, 終了), ...] 「今押してください」の時間帯

    戻り値は候補のリスト。ヒット数の多い順、ノイズの少ない順に並ぶ。
    """
    b0, b1 = baseline_span
    baseline_len = max(b1 - b0, 1e-9)

    rows = []
    for addr in rec.first_seen:
        noise = sum(1 for t, a in rec.changes if a == addr and b0 <= t < b1)
        hits = 0
        for w0, w1 in windows:
            if any(a == addr and w0 <= t <= w1 for t, a in rec.changes):
                hits += 1
        # 押下ウィンドウで初めて現れた機器も候補 (普段は電波を出さないボタン)
        appeared = any(w0 <= rec.first_seen[addr] <= w1 for w0, w1 in windows)
        rows.append({
            "addr": addr,
            "type": rec.types[addr],
            "hits": hits,
            "rounds": len(windows),
            "noise_per_min": noise / baseline_len * 60.0,
            "appeared_on_press": appeared,
        })

    # 全ウィンドウで反応し、かつ普段静かなものを上位に
    rows.sort(key=lambda r: (-r["hits"], r["noise_per_min"], r["addr"]))
    return rows


def verdict(row):
    """候補の評価を一言で返す。"""
    if row["appeared_on_press"]:
        return "★ 押したときだけ現れた"
    if row["hits"] == row["rounds"] and row["noise_per_min"] < 1.0:
        return "★ 毎回反応し、普段は静か"
    if row["hits"] == row["rounds"]:
        return "毎回反応するが、普段もよく変化する"
    if row["hits"] == 0:
        return "反応なし"
    return f"{row['rounds']}回中{row['hits']}回だけ反応"
