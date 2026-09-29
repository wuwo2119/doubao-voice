"""
豆包双向流式语音合成 WebSocket 示例

API Key 获取优先级: 环境变量 DOUBAO_API_KEY > openclaw.json 内置配置（不推荐）
申请方法见仓库根目录 SETUP.md

使用方法:
1. pip install -r requirements.txt
2. 设置环境变量 DOUBAO_API_KEY=你的Key
3. py tts_demo.py
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path

import websockets

from protocols import (
    EventType,
    MsgType,
    finish_connection,
    finish_session,
    receive_message,
    start_connection,
    start_session,
    task_request,
    wait_for_event,
    EVENT_NAMES,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ========== 配置项 ==========
# 取 Key 优先级：环境变量 DOUBAO_API_KEY > 内置（本包默认为空，需自行设置）
import os
API_KEY = os.getenv("DOUBAO_API_KEY", "");
RESOURCE_ID = "seed-tts-2.0"  # 模型版本: seed-tts-2.0 (普通合成) / seed-icl-2.0 (声音复刻)
URL = "wss://openspeech.bytedance.com/api/v3/tts/bidirection"

# ========== 合成参数配置 ==========
SPEAKER = "zh_female_vv_uranus_bigtts"  # 音色ID (vv女声)，可在控制台音色库获取更多音色
AUDIO_FORMAT = "mp3"  # 音频格式: mp3 / pcm / ogg_opus / wav (流式推荐pcm)
SAMPLE_RATE = 24000  # 采样率: 8000/16000/22050/24000/32000/44100/48000
SPEECH_RATE = 0  # 语速: [-50, 100]，100=2倍速，-50=0.5倍速
LOUDNESS_RATE = 0  # 音量: [-50, 100]，100=2倍音量
PITCH = 0  # 音调: [-12, 12]
ENABLE_SUBTITLE = False  # 是否开启字级别时间戳

# 要合成的文本
TEXT = "亲爱的，我好喜欢你"

# ========== 输出配置 ==========
# 输出目录：脚本同级的 output/ 文件夹，不存在时自动创建
OUTPUT_DIR = Path(__file__).resolve().parent / "output"
# ============================


def build_output_path(speaker: str = SPEAKER, output_dir: Path = None) -> Path:
    """生成不重复的输出文件路径（时间戳 + 音色，避免覆盖历史文件）

    Args:
        speaker: 音色ID，用于文件名
        output_dir: 输出目录，默认脚本同级 output/
    """
    out_dir = Path(output_dir) if output_dir else OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"tts_{timestamp}_{speaker}.mp3"
    # 防碰撞：同一秒内多次运行时追加序号
    seq = 1
    while path.exists():
        path = out_dir / f"tts_{timestamp}_{speaker}_{seq}.mp3"
        seq += 1
    return path


async def synthesize_text(
    text: str,
    output_file: str = "output.mp3",
    *,
    api_key: str = None,
    resource_id: str = None,
    speaker: str = None,
    audio_format: str = None,
    sample_rate: int = None,
    speech_rate: int = None,
    loudness_rate: int = None,
    pitch: int = None,
    enable_subtitle: bool = None,
):
    """
    流式合成文本到音频文件

    Args:
        text: 待合成文本
        output_file: 输出音频文件路径
        api_key/resource_id/speaker/audio_format/sample_rate/speech_rate/
        loudness_rate/pitch/enable_subtitle: 可选覆盖，None 时使用模块默认值

    Returns:
        dict: output_file / audio_size / usage / subtitle
    """
    # 参数解析：显式传入优先，否则用模块默认值
    _api_key = api_key or API_KEY
    if not _api_key:
        raise RuntimeError(
            "API Key 未配置：请设置环境变量 DOUBAO_API_KEY，或用 --api-key 参数传入。申请方法见仓库根目录 SETUP.md"
        )
    _resource_id = resource_id or RESOURCE_ID
    _speaker = speaker or SPEAKER
    _format = audio_format or AUDIO_FORMAT
    _sample_rate = sample_rate or SAMPLE_RATE
    _speech_rate = SPEECH_RATE if speech_rate is None else speech_rate
    _loudness_rate = LOUDNESS_RATE if loudness_rate is None else loudness_rate
    _pitch = PITCH if pitch is None else pitch
    _subtitle = ENABLE_SUBTITLE if enable_subtitle is None else enable_subtitle

    connect_id = str(uuid.uuid4())
    headers = {
        "X-Api-Key": _api_key,
        "X-Api-Resource-Id": _resource_id,
        "X-Api-Connect-Id": connect_id,
        "X-Control-Require-Usage-Tokens-Return": "*",
    }

    logger.info(f"正在连接到 {URL}")
    async with websockets.connect(
        URL, additional_headers=headers, max_size=10 * 1024 * 1024
    ) as websocket:
        logid = websocket.response.headers.get('x-tt-logid', 'unknown')
        logger.info(f"连接成功, LogID: {logid}")

        # 1. 建立连接
        await start_connection(websocket)
        conn_resp = await wait_for_event(websocket, MsgType.FULL_SERVER_RESPONSE, EventType.ConnectionStarted)
        logger.info(f"连接已建立, ConnectID: {conn_resp.connection_id}")

        audio_received = False

        # 2. 会话基础参数
        req_params = {
            "speaker": _speaker,
            "audio_params": {
                "format": _format,
                "sample_rate": _sample_rate,
                "speech_rate": _speech_rate,
                "loudness_rate": _loudness_rate,
                "enable_subtitle": _subtitle,
            },
            "post_process": {
                "pitch": _pitch
            }
        }

        # 3. 启动会话
        session_id = str(uuid.uuid4())
        await start_session(websocket, req_params, session_id)
        sess_resp = await wait_for_event(websocket, MsgType.FULL_SERVER_RESPONSE, EventType.SessionStarted)
        logger.info(f"会话已启动, SessionID: {sess_resp.session_id or session_id}")

        # 4. 流式发送字符（逐字发送模拟流式输入）
        async def send_text_stream():
            """流式发送文本，可替换为从LLM等流式数据源逐块发送"""
            # 方式1: 逐字发送，模拟流式输入
            for char in text:
                await task_request(websocket, {"text": char}, session_id)
                await asyncio.sleep(0.01)  # 发送间隔，可根据实际流式输入调整

            # 方式2: 一次性发送全部文本
            # await task_request(websocket, {"text": text}, session_id)

            # 发送完毕，结束会话
            await finish_session(websocket, session_id)

        send_task = asyncio.create_task(send_text_stream())

        # 5. 接收音频数据
        audio_data = bytearray()
        subtitle_data = []
        usage = None

        while True:
            msg = await receive_message(websocket)

            if msg.msg_type == MsgType.FULL_SERVER_RESPONSE:
                event_name = EVENT_NAMES.get(msg.event, str(msg.event))
                logger.debug(f"收到事件: {event_name}")

                if msg.event == EventType.TTSSentenceStart:
                    logger.debug("开始合成句子")
                elif msg.event == EventType.TTSSentenceEnd:
                    sentence_text = msg.payload.get("text", "")
                    logger.debug(f"句子合成结束: {sentence_text}")
                elif msg.event == EventType.TTSSubtitle and _subtitle:
                    subtitle_data.append(msg.payload.get("words", []))
                elif msg.event == EventType.SessionFinished:
                    usage = msg.payload.get("usage", {})
                    logger.info(f"会话结束，计费字符数: {usage.get('text_words', 0)}")
                    break
                elif msg.event in (EventType.ConnectionFailed, EventType.SessionFailed):
                    raise RuntimeError(f"合成失败: {msg.payload}")

            elif msg.msg_type == MsgType.AUDIO_ONLY_SERVER:
                audio_received = True
                audio_data.extend(msg.audio)
                logger.debug(f"收到音频片段: {msg.payload_size} 字节")

        await send_task

        # 6. 保存音频文件
        if audio_data:
            output_path = Path(output_file)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(audio_data)
            logger.info(f"音频已保存到: {output_path.resolve()}，大小: {len(audio_data)} 字节")
        else:
            raise RuntimeError("未收到任何音频数据")

        # 7. 结束连接
        await finish_connection(websocket)
        await wait_for_event(websocket, MsgType.FULL_SERVER_RESPONSE, EventType.ConnectionFinished)
        logger.info("连接已关闭")

        return {
            "output_file": str(output_path.resolve()),
            "audio_size": len(audio_data),
            "usage": usage,
            "subtitle": subtitle_data if _subtitle else None,
        }


async def main():
    """主函数"""
    output_file = build_output_path()
    try:
        result = await synthesize_text(TEXT, output_file)
        print("\n" + "=" * 50)
        print("合成完成！")
        print(f"输出文件: {result['output_file']}")
        print(f"音频大小: {result['audio_size']} 字节")
        print(f"计费字符数: {result['usage'].get('text_words', 0)}")
        print("=" * 50)
    except Exception as e:
        logger.error(f"合成失败: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    asyncio.run(main())
