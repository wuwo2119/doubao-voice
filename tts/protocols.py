"""
豆包双向流式TTS WebSocket v3 协议处理模块
基于火山引擎官方SDK实现
"""
import gzip
import json
import logging
import struct
import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional

logger = logging.getLogger(__name__)

# ========== 协议常量 ==========
PROTOCOL_VERSION = 0b0001
DEFAULT_HEADER_SIZE = 0b0001
INT_SIZE = 4

# 消息序列化方法
NO_SERIALIZATION = 0b0000
JSON_SERIALIZATION = 0b0001

# 消息压缩方式
NO_COMPRESSION = 0b0000
GZIP_COMPRESSION = 0b0001


# ========== 消息类型 ==========
class MsgType(IntEnum):
    FULL_CLIENT_REQUEST = 0b0001
    AUDIO_ONLY_REQUEST = 0b0010
    FULL_SERVER_RESPONSE = 0b1001
    AUDIO_ONLY_SERVER = 0b1011
    FRONT_END_RESULT_SERVER = 0b1100
    SERVER_ERROR_RESPONSE = 0b1111


# ========== 消息标志位 ==========
class MsgTypeFlagBits(IntEnum):
    NO_SEQ = 0b0000
    POSITIVE_SEQ = 0b0001
    LAST_NO_SEQ = 0b0010
    NEGATIVE_SEQ = 0b0011
    WITH_EVENT = 0b0100


# ========== 事件类型（数字编码） ==========
class EventType(IntEnum):
    # 请求事件
    EventNone = 0
    StartConnection = 1
    FinishConnection = 2
    StartSession = 100
    FinishSession = 102
    CancelSession = 103
    TaskRequest = 200
    # 响应事件
    ConnectionStarted = 50
    ConnectionFailed = 51
    ConnectionFinished = 52
    SessionStarted = 150
    SessionFinished = 152
    SessionFailed = 153
    SessionCanceled = 154
    TTSSentenceStart = 350
    TTSSentenceEnd = 351
    TTSResponse = 352
    TTSSubtitle = 353


# ========== 事件名称映射 ==========
EVENT_NAMES = {
    0: "None",
    1: "StartConnection",
    2: "FinishConnection",
    50: "ConnectionStarted",
    51: "ConnectionFailed",
    52: "ConnectionFinished",
    100: "StartSession",
    102: "FinishSession",
    103: "CancelSession",
    150: "SessionStarted",
    152: "SessionFinished",
    153: "SessionFailed",
    154: "SessionCanceled",
    200: "TaskRequest",
    350: "TTSSentenceStart",
    351: "TTSSentenceEnd",
    352: "TTSResponse",
    353: "TTSSubtitle",
}


@dataclass
class TTSMessage:
    """解析后的TTS消息"""
    msg_type: MsgType
    event: EventType
    audio_only: bool = False
    session_finished: bool = False
    session_id: Optional[str] = None
    connection_id: Optional[str] = None
    payload: dict = field(default_factory=dict)
    payload_size: int = 0
    audio: bytes = b""


# ========== 二进制协议处理 ==========

def _pack_message(
    event: int,
    payload_json: str,
    msg_type: MsgType = MsgType.FULL_CLIENT_REQUEST,
    flag: int = MsgTypeFlagBits.WITH_EVENT,
    session_id: str = None,
    connection_id: str = None,
) -> bytes:
    """
    打包客户端消息
    二进制结构:
    [header(4B)] [event(4B)] [connection_id_len(4B) + connection_id?] [session_id_len(4B) + session_id?] [payload_len(4B)] [payload]
    """
    # header
    version_and_header_size = (PROTOCOL_VERSION << 4) | DEFAULT_HEADER_SIZE  # byte0
    type_and_flag = (msg_type << 4) | flag  # byte1
    serialization_and_compression = (JSON_SERIALIZATION << 4) | NO_COMPRESSION  # byte2
    reserved = 0x00  # byte3
    header = struct.pack("BBBB", version_and_header_size, type_and_flag, serialization_and_compression, reserved)

    # body
    frame = bytearray()
    # event code (4 bytes)
    frame.extend(struct.pack(">I", event))

    # connection_id (if any) - 仅connection级事件不需要session_id
    if connection_id is not None:
        conn_id_bytes = connection_id.encode("utf-8")
        frame.extend(struct.pack(">I", len(conn_id_bytes)))
        frame.extend(conn_id_bytes)

    # session_id (if any)
    if session_id is not None and event not in (EventType.StartConnection, EventType.FinishConnection,
                                                  EventType.ConnectionStarted, EventType.ConnectionFailed,
                                                  EventType.ConnectionFinished):
        sess_id_bytes = session_id.encode("utf-8")
        frame.extend(struct.pack(">I", len(sess_id_bytes)))
        frame.extend(sess_id_bytes)

    # payload
    payload_bytes = payload_json.encode("utf-8")
    frame.extend(struct.pack(">I", len(payload_bytes)))
    frame.extend(payload_bytes)

    return bytes(header) + bytes(frame)


def _parse_server_message(data: bytes) -> TTSMessage:
    """解析服务端消息（基于官方SDK实现）"""
    if len(data) < 4:
        raise ValueError(f"Invalid message, too short: {len(data)} bytes")

    # 解析header
    byte0, byte1, byte2, byte3 = struct.unpack("BBBB", data[:4])
    header_size = byte0 & 0x0F
    message_type_bits = byte1 & 0xF0  # 高4位是消息类型
    msg_type_value = message_type_bits >> 4
    flags = byte1 & 0x0F
    serialization = (byte2 >> 4) & 0x0F
    compression = byte2 & 0x0F

    # 映射消息类型
    msg_type_map = {
        0b0001: MsgType.FULL_CLIENT_REQUEST,
        0b0010: MsgType.AUDIO_ONLY_REQUEST,
        0b1001: MsgType.FULL_SERVER_RESPONSE,
        0b1011: MsgType.AUDIO_ONLY_SERVER,
        0b1100: MsgType.FRONT_END_RESULT_SERVER,
        0b1111: MsgType.SERVER_ERROR_RESPONSE,
    }
    msg_type = msg_type_map.get(msg_type_value, MsgType.SERVER_ERROR_RESPONSE)

    result = TTSMessage(msg_type=msg_type, event=EventType.EventNone)
    ptr = header_size * 4  # 跳过header

    # 检查是否包含event字段（WITH_EVENT flag）
    has_event = (flags & MsgTypeFlagBits.WITH_EVENT) != 0

    if has_event:
        # 读取event code (4 bytes)
        event_code = struct.unpack(">i", data[ptr:ptr + INT_SIZE])[0]
        ptr += INT_SIZE
        try:
            result.event = EventType(event_code)
        except ValueError:
            result.event = event_code
            logger.warning(f"Unknown event code: {event_code}")

        # SessionFinished特殊标记
        if event_code == EventType.SessionFinished:
            result.session_finished = True

        # 读取session_id（除了连接级事件）
        if event_code not in (EventType.StartConnection, EventType.FinishConnection,
                              EventType.ConnectionStarted, EventType.ConnectionFailed,
                              EventType.ConnectionFinished):
            session_id_len = struct.unpack(">I", data[ptr:ptr + INT_SIZE])[0]
            ptr += INT_SIZE
            result.session_id = data[ptr:ptr + session_id_len].decode("utf-8")
            ptr += session_id_len

        # 读取connection_id（连接级事件）
        if event_code in (EventType.ConnectionStarted, EventType.ConnectionFailed, EventType.ConnectionFinished):
            conn_id_len = struct.unpack(">I", data[ptr:ptr + INT_SIZE])[0]
            ptr += INT_SIZE
            result.connection_id = data[ptr:ptr + conn_id_len].decode("utf-8")
            ptr += conn_id_len

    # 读取payload
    payload_size = struct.unpack(">i", data[ptr:ptr + INT_SIZE])[0]
    ptr += INT_SIZE
    result.payload_size = payload_size

    payload_data = data[ptr:ptr + payload_size] if payload_size > 0 else b""

    # 解压
    if compression == GZIP_COMPRESSION:
        payload_data = gzip.decompress(payload_data)

    # 解析payload
    if serialization == JSON_SERIALIZATION:
        try:
            result.payload = json.loads(payload_data.decode("utf-8"))
        except Exception as e:
            logger.error(f"Failed to parse JSON payload: {e}, raw: {payload_data[:200]}")
            result.payload = {"raw": payload_data.decode("utf-8", errors="replace")}
    elif serialization == NO_SERIALIZATION:
        result.audio_only = True
        result.audio = payload_data
    else:
        result.audio = payload_data

    # 错误处理
    if msg_type == MsgType.SERVER_ERROR_RESPONSE:
        error_msg = result.payload.get("error", result.payload.get("message", str(result.payload)))
        raise RuntimeError(f"Server error (event={result.event}): {error_msg}")

    return result


# ========== 公共API函数 ==========

async def start_connection(websocket):
    """发送建立连接请求"""
    msg = _pack_message(
        event=EventType.StartConnection,
        payload_json="{}",
        msg_type=MsgType.FULL_CLIENT_REQUEST,
        flag=MsgTypeFlagBits.WITH_EVENT,
    )
    await websocket.send(msg)


async def finish_connection(websocket):
    """发送结束连接请求"""
    msg = _pack_message(
        event=EventType.FinishConnection,
        payload_json="{}",
        msg_type=MsgType.FULL_CLIENT_REQUEST,
        flag=MsgTypeFlagBits.WITH_EVENT,
    )
    await websocket.send(msg)


async def start_session(websocket, req_params: dict, session_id: str, connection_id: str = None):
    """发送创建会话请求"""
    payload = {
        "event": EventType.StartSession,
        "req_params": req_params,
    }
    msg = _pack_message(
        event=EventType.StartSession,
        payload_json=json.dumps(payload),
        msg_type=MsgType.FULL_CLIENT_REQUEST,
        flag=MsgTypeFlagBits.WITH_EVENT,
        session_id=session_id,
        connection_id=connection_id,
    )
    await websocket.send(msg)


async def finish_session(websocket, session_id: str):
    """发送结束会话请求"""
    msg = _pack_message(
        event=EventType.FinishSession,
        payload_json="{}",
        msg_type=MsgType.FULL_CLIENT_REQUEST,
        flag=MsgTypeFlagBits.WITH_EVENT,
        session_id=session_id,
    )
    await websocket.send(msg)


async def cancel_session(websocket, session_id: str):
    """发送取消会话请求"""
    msg = _pack_message(
        event=EventType.CancelSession,
        payload_json="{}",
        msg_type=MsgType.FULL_CLIENT_REQUEST,
        flag=MsgTypeFlagBits.WITH_EVENT,
        session_id=session_id,
    )
    await websocket.send(msg)


async def task_request(websocket, req_params: dict, session_id: str):
    """发送合成任务请求"""
    payload = {
        "event": EventType.TaskRequest,
        "req_params": req_params,
    }
    msg = _pack_message(
        event=EventType.TaskRequest,
        payload_json=json.dumps(payload),
        msg_type=MsgType.FULL_CLIENT_REQUEST,
        flag=MsgTypeFlagBits.WITH_EVENT,
        session_id=session_id,
    )
    await websocket.send(msg)


async def receive_message(websocket) -> TTSMessage:
    """接收并解析服务端消息"""
    data = await websocket.recv()
    return _parse_server_message(data)


async def wait_for_event(websocket, msg_type: MsgType, event_type: EventType, timeout: float = 15.0):
    """等待指定事件"""
    import asyncio
    while True:
        msg = await asyncio.wait_for(receive_message(websocket), timeout=timeout)
        if msg.msg_type == msg_type and msg.event == event_type:
            return msg
        # 错误事件抛出
        if msg.msg_type == MsgType.SERVER_ERROR_RESPONSE:
            raise RuntimeError(f"Error event received: {msg.payload}")
        if msg.event in (EventType.ConnectionFailed, EventType.SessionFailed):
            raise RuntimeError(f"Event {EVENT_NAMES.get(msg.event, msg.event)} failed: {msg.payload}")


def generate_uuid() -> str:
    """生成UUID"""
    return str(uuid.uuid4())
