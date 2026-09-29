#!/usr/bin/env python3
"""Probe whether a Volcengine voice API key can open a WebSocket handshake
on the SAUC ASR endpoint (101 = accepted).

零依赖: 只用标准库 socket + ssl 手搓 WS 握手, 不需要 pip install。

用法:
  py probe_asr_key.py <apiKey> [resourceId]
  py probe_asr_key.py                    # 从环境变量 DOUBAO_API_KEY 读,
                                          # resourceId 默认 volc.seedasr.sauc.duration
退出码:
  0 = 握手成功(101)
  1 = 被拒绝(非101, 通常是 401 key无效 / 403 资源未授权, 错误体打印到 stderr)
  3 = 没有 key
  4 = 网络连接失败
"""
import base64
import os
import socket
import ssl
import sys

HOST = "openspeech.bytedance.com"
PATH = "/api/v3/sauc/bigmodel_async"
DEFAULT_RESOURCE = "volc.seedasr.sauc.duration"


def main():
    args = sys.argv[1:]
    key = args[0] if len(args) > 0 and not args[0].startswith("--") else os.environ.get("DOUBAO_API_KEY", "")
    resource = args[1] if len(args) > 1 else DEFAULT_RESOURCE
    if not key:
        print("Usage: py probe_asr_key.py <apiKey> [resourceId]", file=sys.stderr)
        print("(or set env DOUBAO_API_KEY first)", file=sys.stderr)
        sys.exit(3)

    ctx = ssl.create_default_context()
    try:
        raw = socket.create_connection((HOST, 443), timeout=15)
    except Exception as e:
        print("CONNECT_ERR: %s" % e, file=sys.stderr)
        sys.exit(4)
    ss = ctx.wrap_socket(raw, server_hostname=HOST)

    wskey = base64.b64encode(os.urandom(16)).decode()
    req = (
        "GET %s HTTP/1.1\r\n"
        "Host: %s\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Key: %s\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "X-Api-Key: %s\r\n"
        "X-Api-Resource-Id: %s\r\n"
        "\r\n"
    ) % (PATH, HOST, wskey, key, resource)
    ss.sendall(req.encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = ss.recv(4096)
        if not chunk:
            print("EOF_BEFORE_RESPONSE: server closed", file=sys.stderr)
            sys.exit(4)
        buf += chunk
    first = buf.split(b"\r\n")[0].decode()
    body = buf[buf.index(b"\r\n\r\n") + 4:]

    print("resource: %s" % resource)
    print("status:   %s" % first)
    if "101" not in first:
        print("body: " + body.decode("utf-8", "replace")[:500], file=sys.stderr)
        if "401" in first:
            print("=> 401: API Key invalid (typo or deleted)", file=sys.stderr)
        elif "403" in first:
            print("=> 403: key is valid but this X-Api-Resource-Id is not granted to it", file=sys.stderr)
        sys.exit(1)

    print("OK: websocket handshake accepted (101) - key works")
    ss.close()
    sys.exit(0)


if __name__ == "__main__":
    main()
