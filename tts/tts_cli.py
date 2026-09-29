#!/usr/bin/env python3
"""
豆包双向流式语音合成 CLI（供 OpenClaw / Agent 调用）

设计约定：
- stdout 最终输出一行 JSON 结果（机器可读）
- 日志全部走 stderr（不污染 stdout）
- 退出码：0=成功，1=失败

示例：
  py tts_cli.py "你好，世界"
  py tts_cli.py --text "你好" --speaker zh_female_vv_uranus_bigtts
  py tts_cli.py --file input.txt --output-dir D:/audio
  echo "你好" | py tts_cli.py -
"""
import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

# 确保能 import 同目录的模块（打包后自包含）
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tts_demo import build_output_path, synthesize_text  # noqa: E402

STDERR_LOGGER_NAME = "tts_cli"


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="tts_cli.py",
        description="豆包双向流式语音合成 CLI。成功时 stdout 输出 JSON，日志走 stderr。",
    )

    # 文本输入（三选一：位置参数 / --text / --file / 管道 -）
    parser.add_argument("text_pos", nargs="?", help="待合成文本（直接跟在命令后）")
    parser.add_argument("--text", "-t", help="待合成文本")
    parser.add_argument("--file", "-f", help="从 UTF-8 文本文件读取待合成文本")
    parser.add_argument("--stdin", action="store_true",
                        help="从标准输入读取文本（也支持用 - 作为位置参数）")

    # 合成参数
    parser.add_argument("--speaker", "-s", default=None,
                        help="音色ID，默认 zh_female_vv_uranus_bigtts")
    parser.add_argument("--format", choices=["mp3", "pcm", "ogg_opus", "wav"],
                        default=None, help="音频格式，默认 mp3")
    parser.add_argument("--sample-rate", type=int, default=None,
                        help="采样率(Hz)：8000/16000/22050/24000/32000/44100/48000，默认 24000")
    parser.add_argument("--speech-rate", type=int, default=None,
                        help="语速 [-50,100]，100=2倍速，默认 0")
    parser.add_argument("--loudness-rate", type=int, default=None,
                        help="音量 [-50,100]，100=2倍音量，默认 0")
    parser.add_argument("--pitch", type=int, default=None,
                        help="音调 [-12,12]，默认 0")
    parser.add_argument("--subtitle", action="store_true",
                        help="开启字幕时间戳（字幕数据包含在返回 JSON 中）")

    # 鉴权与协议
    parser.add_argument("--api-key", default=None,
                        help="覆盖 API Key（默认用内置配置）")
    parser.add_argument("--resource-id", default=None,
                        help="模型版本：seed-tts-2.0(默认) / seed-icl-2.0(声音复刻)")

    # 输出
    parser.add_argument("--output-dir", "-o", default=None,
                        help="输出目录，默认脚本同级 output/（自动创建，时间戳命名不覆盖）")
    parser.add_argument("--output", default=None,
                        help="直接指定输出文件路径（与 --output-dir 互斥）")

    # 行为
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="静默模式：不输出 stderr 日志（仅保留 stdout JSON）")
    parser.add_argument("--pretty", action="store_true",
                        help="JSON 结果格式化缩进输出")
    return parser.parse_args(argv)


def read_text(args: argparse.Namespace) -> str:
    """按优先级取文本：位置参数 > --text > --file > stdin(-/--stdin)"""
    if args.text_pos == "-" or args.stdin:
        text = sys.stdin.read()
    elif args.text_pos:
        text = args.text_pos
    elif args.text:
        text = args.text
    elif args.file:
        text = Path(args.file).read_text(encoding="utf-8").strip()
    else:
        raise SystemExit("错误：未提供合成文本。用法：tts_cli.py \"文本\" 或 --text/--file/-")

    text = text.strip()
    if not text:
        raise SystemExit("错误：合成文本为空")
    if len(text) > 5000:
        print(f"警告：文本长度 {len(text)}，超长可能影响合成质量", file=sys.stderr)
    return text


def main() -> int:
    args = parse_args()

    # 日志配置：全部走 stderr；quiet 时不输出
    level = logging.CRITICAL if args.quiet else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
        force=True,
    )

    try:
        text = read_text(args)
    except SystemExit as e:
        if e.code and isinstance(e.code, str):
            print(e.code, file=sys.stderr)
            result = {"success": False, "error": e.code}
            print(json.dumps(result, ensure_ascii=False))
            return 1
        raise

    # 确定输出路径
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = build_output_path(
            speaker=args.speaker or "default",
            output_dir=Path(args.output_dir) if args.output_dir else None,
        )

    # 执行合成
    try:
        result = asyncio.run(
            synthesize_text(
                text,
                str(output_path),
                api_key=args.api_key,
                resource_id=args.resource_id,
                speaker=args.speaker,
                audio_format=args.format,
                sample_rate=args.sample_rate,
                speech_rate=args.speech_rate,
                loudness_rate=args.loudness_rate,
                pitch=args.pitch,
                enable_subtitle=args.subtitle or None,
            )
        )
    except Exception as e:
        error_result = {
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__,
            "text_length": len(text),
        }
        print(json.dumps(error_result, ensure_ascii=False))
        if not args.quiet:
            import traceback
            traceback.print_exc(file=sys.stderr)
        return 1

    # stdout 输出机器可读 JSON 结果
    output = {
        "success": True,
        "output_file": result["output_file"],
        "audio_size": result["audio_size"],
        "text_length": len(text),
        "billed_chars": (result.get("usage") or {}).get("text_words"),
        "speaker": args.speaker or "zh_female_vv_uranus_bigtts",
        "format": args.format or "mp3",
    }
    if result.get("subtitle"):
        output["subtitle"] = result["subtitle"]

    indent = 2 if args.pretty else None
    print(json.dumps(output, ensure_ascii=False, indent=indent))
    return 0


if __name__ == "__main__":
    sys.exit(main())
