#!/usr/bin/env python3
# coding: utf-8
"""音声アナウンスの生成と再生。

espeak-ng で WAV を作って aplay で鳴らす。同じ文面の WAV は使い回すので、
生成は初回だけ。ラズパイ側に espeak-ng が必要:

    sudo apt install -y espeak-ng alsa-utils
"""
import hashlib
import logging
import pathlib
import shutil
import subprocess

log = logging.getLogger("switchbot")

SOUND_DIR = pathlib.Path(__file__).resolve().parent / "sounds"


class AudioError(RuntimeError):
    """音声の生成や再生に失敗した。対処方法を message に含める。"""


def _require(cmd, apt_package):
    if shutil.which(cmd) is None:
        raise AudioError(f"{cmd} が見つかりません。sudo apt install -y {apt_package}")
    return cmd


def wav_path(message, voice, speed):
    """文面と読み上げ設定ごとに WAV のパスを決める。"""
    key = f"{message}|{voice}|{speed}".encode("utf-8")
    digest = hashlib.sha1(key).hexdigest()[:12]
    return SOUND_DIR / f"announce_{digest}.wav"


def build_wav(message, voice="en", speed=150, force=False):
    """文面から WAV を生成する。既にあれば作り直さない。"""
    path = wav_path(message, voice, speed)
    if path.exists() and not force:
        return path

    _require("espeak-ng", "espeak-ng")
    SOUND_DIR.mkdir(exist_ok=True)
    log.info("音声を生成しています: %s", path.name)
    proc = subprocess.run(
        ["espeak-ng", "-v", voice, "-s", str(speed), "-w", str(path), message],
        capture_output=True, text=True)
    if proc.returncode != 0 or not path.exists():
        raise AudioError(f"espeak-ng が失敗しました: {proc.stderr.strip()}")
    return path


def play(message, voice="en", speed=150, repeat=2, device=None, gap=1.0):
    """アナウンスを鳴らす。repeat 回くり返す。"""
    import time

    path = build_wav(message, voice, speed)
    _require("aplay", "alsa-utils")

    cmd = ["aplay", "-q"]
    if device:
        cmd += ["-D", device]
    cmd.append(str(path))

    for i in range(repeat):
        if i:
            time.sleep(gap)
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise AudioError(
                f"aplay が失敗しました: {proc.stderr.strip()}\n"
                "  出力先を確認してください: aplay -l で一覧、--device plughw:1,0 のように指定")
    log.info("アナウンスを再生しました (%d回): %s", repeat, message)
