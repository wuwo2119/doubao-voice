#!/usr/bin/env python3
"""Volcengine bigspeech SAUC bidirectional streaming ASR 2.0 — transcription CLI.

Endpoint: wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async
Auth:     X-Api-Key + X-Api-Resource-Id (new-console auth)
Audio:    any format ffmpeg can read -> 16kHz/16bit/mono WAV, 200ms packets
Deps:     python websockets, ffmpeg on PATH (or --ffmpeg <path>)
Out:      transcript text on stdout; optional JSON via --out

Usage:
  python sauc_asr_cli.py <audio_file> [options]

Options:
  --key <X-Api-Key>            API key (priority: --key > env DOUBAO_API_KEY >
                               openclaw.json doubao apiKey, if openclaw.json exists)
  --resource <id>             X-Api-Resource-Id (default volc.seedasr.sauc.duration)
  --ffmpeg <path>             ffmpeg executable (default: search PATH)
  --url <wss-url>             endpoint (default bigmodel_async)
  --seg <ms>                  segment duration (default 200)
  --out <path>                also write JSON result to path
  --quiet                     no stderr progress logs
"""
import asyncio
import gzip
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time as _time
import wave
from pathlib import Path

import websockets

DEFAULT_RESOURCE = "volc.seedasr.sauc.duration"
DEFAULT_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
FFMPEG_CANDIDATES = [
    shutil.which("ffmpeg") or "",
    # 按需在此追加本机 ffmpeg 路径
]


def load_default_key():
    """Read the doubao API key without printing it.
    Priority: env DOUBAO_API_KEY first, then openclaw.json (string apiKey or
    SecretRef file/env source) if present on this machine."""
    env_key = os.environ.get("DOUBAO_API_KEY", "")
    if env_key:
        return env_key
    for p in (
        Path.home() / ".openclaw" / "openclaw.json",
    ):
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        key = (cfg.get("models", {}).get("providers", {}).get("doubao", {}) or {}).get("apiKey") or ""
        if isinstance(key, str) and key:
            return key
        if isinstance(key, dict):
            src = key.get("source", "")
            rid = key.get("id", "")
            if src == "env" and rid:
                v = os.environ.get(rid, "")
                if v:
                    return v
            elif src == "file" and rid:
                # secret value file under .openclaw/secrets (provider/id shaped name)
                base = Path.home() / ".openclaw"
                cand = base / "secrets" / rid
                if cand.is_file():
                    try:
                        v = cand.read_text(encoding="utf-8").strip()
                        if v:
                            return v
                    except Exception:
                        pass
                env_fallback = os.environ.get(rid, "")
                if env_fallback:
                    return env_fallback
    return ""


def find_ffmpeg(cli_override):
    if cli_override:
        return cli_override
    for c in FFMPEG_CANDIDATES:
        if c and Path(c).is_file():
            return c
    raise SystemExit("ffmpeg not found; pass --ffmpeg <path>")


def convert_to_16k_wav(src: str, ffmpeg: str, tmp_dir: Path) -> Path:
    out = tmp_dir / f"{Path(src).stem}_16k.wav"
    cmd = [ffmpeg, "-v", "quiet", "-y", "-i", src, "-acodec", "pcm_s16le", "-ac", "1", "-ar", "16000", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"ffmpeg conversion failed ({src}): {r.stderr[:300]}")
    return out


def build_full(seq: int, payload_obj: dict) -> bytes:
    jsonb = json.dumps(payload_obj, separators=(",", ":")).encode("utf-8")
    p = gzip.compress(jsonb)
    buf = bytearray()
    buf.append(0x11)
    buf.append((1 << 4) | 1)
    buf.append(0x11)
    buf.append(0)
    buf += struct.pack(">i", seq)
    buf += struct.pack(">I", len(p))
    buf += p
    return bytes(buf)


def build_audio(seq: int, segment: bytes, last: bool) -> bytes:
    # Mirrors official sauc_python RequestBuilder.new_audio_only_request exactly:
    # byte2 stays 0x11 (ser=JSON, comp=GZIP) because the payload is gzip-compressed.
    p = gzip.compress(segment)
    seq_signed = -seq if last else seq
    flags = 3 if last else 1
    buf = bytearray()
    buf.append(0x11)
    buf.append((2 << 4) | flags)
    buf.append(0x11)
    buf.append(0)
    buf += struct.pack(">i", seq_signed)
    buf += struct.pack(">I", len(p))
    buf += p
    return bytes(buf)


def parse_response(msg: bytes):
    """Mirrors official ResponseParser.parse_response. Returns (msgType, flags, ser, comp, payload, is_last, error_code)."""
    header_size = msg[0] & 0x0F
    msgType = msg[1] >> 4
    flags = msg[1] & 0x0F
    ser = msg[2] >> 4
    comp = msg[2] & 0x0F
    payload = msg[header_size * 4:]
    if flags & 1:
        payload = payload[4:]
    is_last = bool(flags & 2)
    if flags & 4:
        payload = payload[4:]
    error_code = 0
    if msgType == 9:
        pass
    elif msgType == 15:
        error_code = struct.unpack_from(">i", payload, 0)[0]
        payload = payload[4:]
    payload_size = struct.unpack_from(">I", payload, 0)[0]
    payload = payload[4:4 + payload_size]
    if comp == 1:
        try:
            payload = gzip.decompress(payload)
        except Exception:
            pass
    return msgType, flags, ser, comp, payload, is_last, error_code


def find_pcm_offset(wav_bytes: bytes) -> int:
    pos = 12
    while pos < len(wav_bytes) - 8:
        sub_id = wav_bytes[pos:pos + 4]
        sub_sz = struct.unpack("<I", wav_bytes[pos + 4:pos + 8])[0]
        if sub_id == b"data":
            return pos + 8
        pos += 8 + sub_sz
    raise ValueError("No data subchunk in WAV")


def strip_wav_to_header_pcm(wav_bytes: bytes) -> bytes:
    """Rebuild a minimal clean 44-byte-header WAV (no LIST/INFO chunks)."""
    pcm_off = find_pcm_offset(wav_bytes)
    pcm = wav_bytes[pcm_off:]
    out = bytearray(44)
    out[0:4] = b"RIFF"
    struct.pack_into("<I", out, 4, 36 + len(pcm))
    out[8:12] = b"WAVE"
    out[12:16] = b"fmt "
    struct.pack_into("<I", out, 16, 16)
    struct.pack_into("<H", out, 20, 1)
    struct.pack_into("<H", out, 22, 1)
    struct.pack_into("<I", out, 24, 16000)
    struct.pack_into("<I", out, 28, 32000)
    struct.pack_into("<H", out, 32, 2)
    struct.pack_into("<H", out, 34, 16)
    out[36:40] = b"data"
    struct.pack_into("<I", out, 40, len(pcm))
    out += pcm
    return bytes(out)


def split_segments(wav_bytes: bytes, seg_size: int) -> list:
    """Official demo semantics: split the RAW WAV byte string at fixed offsets.
    seg[0] therefore includes the WAV header; later segs are raw PCM continuation.
    """
    total = len(wav_bytes)
    out = []
    i = 0
    while i < total:
        end = min(i + seg_size, total)
        out.append(wav_bytes[i:end])
        i = end
    return out


async def transcribe(wav_path: str, key: str, resource: str, url: str, seg_ms: int, verbose: bool):
    wav_bytes = Path(wav_path).read_bytes()
    wav_bytes = strip_wav_to_header_pcm(wav_bytes)

    wv = wave.open(str(wav_path), "rb")
    sample_rate = wv.getframerate()
    channels = wv.getnchannels()
    sample_width = wv.getsampwidth()
    n_frames = wv.getnframes()
    wv.close()

    if sample_rate != 16000:
        raise SystemExit(f"Expected 16kHz WAV, got {sample_rate}Hz (run ffmpeg conversion first)")

    # Official demo: segment_size = bytes_per_sec * seg_ms // 1000
    bytes_per_sec = sample_rate * sample_width * channels
    seg_size = bytes_per_sec * seg_ms // 1000
    segments = split_segments(wav_bytes, seg_size)

    if verbose:
        print(f"[asr] {wav_path} | {sample_rate}Hz {channels}ch | {n_frames/sample_rate:.1f}s | {len(segments)} x {seg_ms}ms", file=sys.stderr)

    extra = {
        "X-Api-Key": key,
        "X-Api-Resource-Id": resource,
        "X-Api-Connect-Id": os.urandom(6).hex(),
    }
    texts = []
    payload = {
        "user": {"uid": "doubao-voice-agent"},
        "audio": {"format": "wav", "codec": "raw", "rate": sample_rate, "bits": sample_width * 8, "channel": channels},
        "request": {
            "model_name": "bigmodel",
            "enable_itn": True,
            "enable_punc": True,
            "enable_ddc": True,
            "show_utterances": True,
            "enable_nonstream": True,
        },
    }

    async with websockets.connect(url, additional_headers=extra, open_timeout=20, close_timeout=20, max_size=None) as ws:
        seq = 1
        await ws.send(build_full(seq, payload))
        seq += 1

        # Mirror the official AsrWsClient: a background SENDER that paces 200ms
        # chunks (non-blocking, fixed tick), and the main task DRAINS the
        # receive stream. This keeps the 8s server-side "waiting next packet"
        # window satisfied. Do NOT block-recv between each send.
        interval = seg_ms / 1000.0

        async def sender():
            nonlocal seq
            next_deadline = _time.monotonic()
            for i in range(len(segments)):
                chunk = segments[i]
                last = i == len(segments) - 1
                await ws.send(build_audio(seq, chunk, last))
                if not last:
                    seq += 1
                # fixed-tick pacing with catch-up, exactly like the demo
                next_deadline += interval
                remaining = next_deadline - _time.monotonic()
                if remaining > 0:
                    await asyncio.sleep(remaining)
                else:
                    next_deadline = _time.monotonic()
                    await asyncio.sleep(0)

        sender_task = asyncio.create_task(sender())

        # Drain responses until last package or server error
        while True:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=30)
            except asyncio.TimeoutError:
                if verbose:
                    print("[asr] recv drained; no more responses", file=sys.stderr)
                break
            mt, fl, sr, cp, p, is_last, ec = parse_response(msg)
            if mt == 9:
                if sr == 1 and p:
                    try:
                        j = json.loads(p)
                        txt = ""
                        if "result" in j:
                            txt = j["result"].get("text", "")
                        elif "utterances" in j:
                            txt = " ".join(u.get("text", "") for u in j["utterances"])
                        if txt:
                            # Stream responses are CUMULATIVE (each partial
                            # contains the whole transcript so far). Keep the
                            # longest one as the final transcript instead of
                            # concatenating every partial.
                            if len(txt) > len(texts[0]) if texts else True:
                                texts = [txt]
                            if verbose:
                                print(f"[asr] part ({len(txt)} chars): {txt[:60]}", file=sys.stderr)
                    except Exception:
                        pass
                if is_last:
                    break
            elif mt == 15:
                detail = p.decode("utf-8", "replace")[:400]
                print(f"[asr] SERVER_ERROR code={ec} {detail}", file=sys.stderr)
                break

        try:
            await sender_task
        except Exception as e:
            if verbose:
                print(f"[asr] sender ended: {e}", file=sys.stderr)

    return "".join(texts)


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("--help", "-h"):
        print(__doc__)
        sys.exit(0)

    positional = [a for a in args if not a.startswith("--")]
    if not positional:
        print(__doc__)
        sys.exit(1)
    audio_file = positional[0]
    if not Path(audio_file).is_file():
        print(f"File not found: {audio_file}", file=sys.stderr)
        sys.exit(1)

    def opt(name, default=None):
        if name in args:
            return args[args.index(name) + 1]
        return default

    key = opt("--key") or load_default_key()
    if not key:
        print("No API key: pass --key, set DOUBAO_API_KEY, or add doubao apiKey to openclaw.json", file=sys.stderr)
        sys.exit(1)
    # Validate the key is a plain credential string, not a SecretRef object
    if isinstance(key, dict):
        print("Could not resolve doubao apiKey SecretRef; pass --key explicitly", file=sys.stderr)
        sys.exit(1)

    resource = opt("--resource", DEFAULT_RESOURCE)
    url = opt("--url", DEFAULT_URL)
    seg_ms = int(opt("--seg", "200"))
    ffmpeg = find_ffmpeg(opt("--ffmpeg"))
    out_path = opt("--out")
    quiet = "--quiet" in args
    verbose = not quiet

    # Convert to 16k mono WAV if needed
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td)
        p = Path(audio_file)
        if p.suffix.lower() == ".wav":
            # verify it's 16k mono s16, otherwise convert
            try:
                wv = wave.open(str(p), "rb")
                ok = wv.getframerate() == 16000 and wv.getnchannels() == 1 and wv.getsampwidth() == 2
                wv.close()
                if ok:
                    target = p
                else:
                    target = convert_to_16k_wav(str(p), ffmpeg, Path(td))
            except Exception:
                target = convert_to_16k_wav(str(p), ffmpeg, Path(td))
        else:
            target = convert_to_16k_wav(str(p), ffmpeg, Path(td))

        text = asyncio.run(transcribe(str(target), key, resource, url, seg_ms, verbose))

    # stdout = transcript only (OpenClaw CLI media entry reads stdout as transcript)
    print(text)
    result = {
        "success": bool(text),
        "text": text,
        "input": audio_file,
        "resource": resource,
    }
    if out_path:
        Path(out_path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        if verbose:
            print(f"[asr] wrote {out_path}", file=sys.stderr)
    sys.exit(0 if text else 2)


if __name__ == "__main__":
    main()
