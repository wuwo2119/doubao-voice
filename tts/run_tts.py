#!/usr/bin/env python3
"""运行 TTS 文字转语音的便捷入口 — 调用同目录的 tts_cli.py。

用法:
  py run_tts.py <文本> [选项]
  py run_tts.py "你好，世界"
  py run_tts.py --file input.txt --speaker zh_female_vv_uranus_bigtts
  py run_tts.py "你好" --api-key 你的KEY --output D:/audio/hello.mp3

依赖: python websockets (py -m pip install -r requirements.txt)
输出: 成功时 stdout 输出一行 JSON (output_file 指向生成的 mp3)
"""
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "tts_cli.py"


def main():
    if not SCRIPT.is_file():
        print(f"找不到 {SCRIPT}", file=sys.stderr)
        sys.exit(1)
    sys.exit(subprocess.call([sys.executable, str(SCRIPT)] + sys.argv[1:]))


if __name__ == "__main__":
    main()
