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
        self.types = {}                 # addr -> 機種バイト (SwitchBot 以外は None)
        self.names = {}                 # addr -> アドバタイズ名

    def record(self, addr, dtype, payload, now, name=None):
        # BLE は ADV_IND と SCAN_RSP が別パケットで届く。機種が分かるのは
        # サービスデータを含む方だけなので、後から判明したら上書きする。
        if dtype is not None:
            self.types[addr] = dtype
        if name:
            self.names[addr] = name

        if addr not in self.first_seen:
            self.first_seen[addr] = now
            self.types.setdefault(addr, dtype)
            self.names.setdefault(addr, name)
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
            "name": rec.names.get(addr),
            "hits": hits,
            "rounds": len(windows),
            "noise_per_min": noise / baseline_len * 60.0,
            "appeared_on_press": appeared,
        })

    # 全ウィンドウで反応し、かつ普段静かなものを上位に
    rows.sort(key=lambda r: (-r["hits"], r["noise_per_min"], r["addr"]))
    return rows


def appearance_rates(rec, baseline_span, windows, settle=3.0):
    """新しい機器が現れる頻度を、待機中と押下中で比べる。

    周囲には MAC アドレスを定期的に変える機器 (スマホ等) が多数あり、押下と
    無関係に「新しい機器」として現れ続ける。待機中も同じ頻度で現れているなら、
    押下中の出現もただの背景ノイズである。

    settle: 計測開始直後は既存機器が一斉に初観測されるため、その分を除く秒数。
    """
    b0, b1 = baseline_span
    idle_len = max(b1 - (b0 + settle), 1e-9)
    idle_new = sum(1 for t in rec.first_seen.values() if b0 + settle <= t < b1)

    press_len = sum(w1 - w0 for w0, w1 in windows) or 1e-9
    press_new = sum(1 for t in rec.first_seen.values()
                    if any(w0 <= t <= w1 for w0, w1 in windows))

    return {
        "idle_per_min": idle_new / idle_len * 60.0,
        "press_per_min": press_new / press_len * 60.0,
        "idle_count": idle_new,
        "press_count": press_new,
    }


def appearances_look_like_noise(rates, factor=2.0):
    """押下中の出現が、待機中と同程度なら背景ノイズとみなす。"""
    if rates["idle_per_min"] <= 0:
        return rates["press_count"] > 3      # 待機中ゼロでも大量なら怪しい
    return rates["press_per_min"] < rates["idle_per_min"] * factor


def new_during_press(rows):
    """押下ウィンドウ中に初めて現れた機器だけを返す。

    リモートは押されたときだけ電波を出すことがあり、その場合「変化」ではなく
    「出現」として現れる。MAC アドレスが毎回変わる機器も、押すたびに別の機器
    として出現するのでここに並ぶ。
    """
    return [r for r in rows if r["appeared_on_press"]]


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
