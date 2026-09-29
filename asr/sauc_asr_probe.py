#!/usr/bin/env python3
"""Probe SAUC ASR WebSocket handshake for the configured resource.
Exit code 0 = handshake OK (101). Non-zero = rejected/unreachable.
Key source: first positional arg > env DOUBAO_ASR_KEY / DOUBAO_API_KEY.
"""
import base64
import os
import socket
import ssl
import sys

HOST = "openspeech.bytedance.com"
PATH = "/api/v3/sauc/bigmodel_async"
DEFAULT_RESOURCE = "volc.seedasr.sauc.duration"


def find_key(argv):
    # 1) positional arg
    if len(argv) > 1 and not argv[1].startswith("--"):
        return argv[1].strip()
    # 2) env
    for name in ("DOUBAO_ASR_KEY", "DOUBAO_API_KEY"):
        k = os.environ.get(name, "")
        if k:
            return k.strip()
    return ""


def main():
    resource = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_RESOURCE
    key = find_key(sys.argv)
    if not key:
        print("Usage: py sauc_asr_probe.py <apiKey> [resourceId]", file=sys.stderr)
        print("(or set env DOUBAO_API_KEY first)", file=sys.stderr)
        sys.exit(3)

    ctx = ssl.create_default_context()
    try:
        raw = socket.create_connection((HOST, 443), timeout=10)
    except Exception as e:
        print("CONNECT_ERR %s" % e, file=sys.stderr)
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
            print("EOF_BEFORE_RESPONSE", file=sys.stderr)
            sys.exit(5)
        buf += chunk
    first = buf.split(b"\r\n")[0].decode()
    body = buf[buf.index(b"\r\n\r\n") + 4:]
    print(resource, "=>", first)
    if "101" not in first:
        print("body:", body.decode("utf-8", "replace")[:300], file=sys.stderr)
        sys.exit(1)
    print("OK: websocket handshake accepted (101)")
    ss.close()
    sys.exit(0)


if __name__ == "__main__":
    main()
