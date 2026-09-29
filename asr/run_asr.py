#!/usr/bin/env python3
"""运行 ASR 语音转文字的便捷入口 — 调用同目录的 sauc_asr_cli.py。

用法:
  py run_asr.py <音频文件> [选项]     # 选项透传给 sauc_asr_cli.py
  py run_asr.py audio.mp3
  py run_asr.py audio.mp3 --out result.json
  py run_asr.py audio.mp3 --key 你的KEY

音频格式: 任意 ffmpeg 能读的格式 (mp3/m4a/wav/ogg/flac...)，自动转 16kHz 单声道 WAV。
依赖: python websockets + ffmpeg (PATH 中, 或传 --ffmpeg <路径>)
"""
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "sauc_asr_cli.py"


def main():
    if not SCRIPT.is_file():
        print(f"找不到 {SCRIPT}", file=sys.stderr)
        sys.exit(1)
    # 透传所有参数
    args = [sys.executable, str(SCRIPT)] + sys.argv[1:]
    sys.exit(subprocess.call(args))


if __name__ == "__main__":
    main()
