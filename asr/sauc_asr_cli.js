#!/usr/bin/env node
/**
 * Volcengine BigSpeech SAUC 双向流式 ASR 2.0 客户端（零依赖）
 * 协议: wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream
 * 鉴权: X-Api-Key + X-Api-Resource-Id (新版控制台)
 * 音频: 16kHz/16bit/单声道 WAV, 200ms 分包, gzip 帧封装
 *
 * 用法:
 *   node sauc_asr_cli.js <wav_file> [--resource volc.bigasr.sauc.duration] [--out out.json]
 */
import https from 'node:https';
import zlib from 'node:zlib';
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

// ---- WebSocket client (raw, no deps) ----
class Ws {
  constructor(url, headers = {}) {
    this.url = url;
    this.headers = headers;
    this.sock = null;
    this.buf = Buffer.alloc(0);
    this.handlers = { open: [], data: [], close: [], error: [], upgrade: [] };
  }
  on(event, fn) { this.handlers[event]?.push(fn); return this; }
  _emit(event, data) { (this.handlers[event] || []).forEach(fn => fn(data)); }
  connect() {
    return new Promise((resolve, reject) => {
      const parsed = new URL(this.url.replace('wss://', 'https:'));
      const host = parsed.hostname;
      const pathStr = parsed.pathname;
      const req = https.request({
        host,
        port: 443,
        path: pathStr,
        method: 'GET',
        headers: {
          'Upgrade': 'websocket',
          'Connection': 'Upgrade',
          'Sec-WebSocket-Key': 'dGhlIHNhbXBsZSBub25jZQ==',
          'Sec-WebSocket-Version': '13',
          ...this.headers,
        },
      }, (res) => {
        if (res.statusCode === 101) {
          const socket = res.socket;
          this.sock = socket;
          this._emit('upgrade', res);
          socket.on('data', (chunk) => this._onSocketData(chunk));
          socket.on('close', () => this._emit('close'));
          socket.on('error', (e) => this._emit('error', e));
          this._emit('open');
          resolve();
        } else {
          let body = '';
          res.on('data', (c) => body += c);
          res.on('end', () => {
            reject(new Error(`HTTP ${res.statusCode}: ${body.slice(0, 200)}`));
          });
        }
      });
      req.on('error', (e) => {
        reject(e);
      });
      req.end();
    });
  }
  _onSocketData(chunk) {
    this.buf = Buffer.concat([this.buf, chunk]);
    // parse WebSocket frames
    while (this.buf.length >= 2) {
      const b0 = this.buf[0], b1 = this.buf[1];
      const opcode = b0 & 0x0f;
      let len = b1 & 0x7f;
      let offset = 2;
      if (len === 126) {
        if (this.buf.length < 4) return;
        len = this.buf.readUInt16BE(2);
        offset = 4;
      } else if (len === 127) {
        if (this.buf.length < 10) return;
        len = Number(this.buf.readBigUInt64BE(2));
        offset = 10;
      }
      if (this.buf.length < offset + len) return;
      const payload = this.buf.subarray(offset, offset + len);
      this.buf = this.buf.subarray(offset + len);
      if (opcode === 0x8) { // close
        this._emit('close');
        this.sock?.destroy();
        return;
      }
      if (opcode === 0x9) { // ping -> pong
        this._sendFrame(Buffer.concat([Buffer.from([0x88]), payload]));
        continue;
      }
      if (opcode === 0x1 || opcode === 0x2) {
        this._emit('data', payload);
      }
    }
  }
  _sendFrame(payload) {
    if (!this.sock) return;
    const mask = Buffer.alloc(4);
    for (let i = 0; i < 4; i++) mask[i] = Math.floor(Math.random() * 256);
    const masked = Buffer.alloc(payload.length);
    for (let i = 0; i < payload.length; i++) masked[i] = payload[i] ^ mask[i % 4];
    let header;
    if (payload.length <= 125) {
      header = Buffer.from([0x82, 0x80 | payload.length]);
    } else if (payload.length <= 65535) {
      header = Buffer.alloc(4);
      header[0] = 0x82; header[1] = 0x80 | 126;
      header.writeUInt16BE(payload.length, 2);
    } else {
      header = Buffer.alloc(10);
      header[0] = 0x82; header[1] = 0x80 | 127;
      header.writeBigUInt64BE(BigInt(payload.length), 2);
    }
    this.sock.write(Buffer.concat([header, mask, masked]));
  }
  sendBinary(buf) { this._sendFrame(buf); }
  close() { this._sendFrame(Buffer.alloc(0)); this.sock?.destroy(); }
}

// ---- SAUC protocol framing (per sauc_python/protocol.py) ----
// Header: byte0 = 0x11 (v1<<4 | 1)
//        byte1 = msgType<<4 | flags
//        byte2 = serType<<4 | compType
//        reserved: 1 byte = 0x00
// Then: 4-byte seq (big-endian signed), then 4-byte payloadSize (big-endian), then payload

function buildRequestHeader(msgType, flags, serType, compType) {
  const buf = Buffer.alloc(4);
  buf[0] = (1 << 4) | 1;
  buf[1] = (msgType << 4) | flags;
  buf[2] = (serType << 4) | compType;
  buf[3] = 0;
  return buf;
}

function buildFullClientRequest(seq, payloadObj) {
  const COMPRESSED = 1;
  const JSON_SER = 1;
  const POS_SEQ = 1;
  const header = buildRequestHeader(1, POS_SEQ, JSON_SER, COMPRESSED); // FULL_CLIENT_REQUEST=0b0001
  const jsonStr = JSON.stringify(payloadObj);
  const compressed = zlib.gzipSync(Buffer.from(jsonStr, 'utf8'));
  const out = Buffer.alloc(4 + 4 + 4 + compressed.length);
  header.copy(out, 0);
  out.writeInt32BE(seq, 4);
  out.writeUInt32BE(compressed.length, 8);
  compressed.copy(out, 12);
  return out;
}

function buildAudioOnlyRequest(seq, segment, isLast) {
  const AUDIO_ONLY = 2;
  const NEG_WITH_SEQ = 3; // 0b0011
  const POS_SEQ = 1;
  const NO_SEQ = 0;
  const NO_COMP = 0;
  const NO_SER = 0;
  let flags = isLast ? NEG_WITH_SEQ : POS_SEQ;
  let s = isLast ? -seq : seq;
  const header = buildRequestHeader(AUDIO_ONLY, flags, NO_SER, NO_COMP);
  const compressed = zlib.gzipSync(Buffer.from(segment));
  const out = Buffer.alloc(4 + 4 + 4 + compressed.length);
  header.copy(out, 0);
  out.writeInt32BE(s, 4);
  out.writeUInt32BE(compressed.length, 8);
  compressed.copy(out, 12);
  return out;
}

function parseServerResponse(msg) {
  // header: byte0 = version<<4|size; byte1 = msgType<<4|flags; byte2 = ser<<4|comp; byte3 = reserved
  const size = msg[0] & 0x0f; // reserved bytes count (1 typically)
  const msgType = msg[1] >> 4;
  const flags = msg[1] & 0x0f;
  const ser = msg[2] >> 4;
  const comp = msg[2] & 0x0f;
  let off = 4 * size; // header is 4*size bytes

  let isLast = false;
  let event = 0;
  let seq = 0;
  let code = 0;

  // Parse flags
  if (flags & 0x01) { seq = msg.readInt32BE(off); off += 4; }
  if (flags & 0x02) { isLast = true; }
  if (flags & 0x04) { event = msg.readInt32BE(off); off += 4; }

  if (msgType === 5) { // SERVER_FULL_RESPONSE
    const payloadSize = msg.readUInt32BE(off); off += 4;
    let payload = msg.subarray(off, off + payloadSize);
    if (comp === 1) {
      try { payload = zlib.gunzipSync(payload); } catch {}
    }
    let obj = null;
    if (ser === 1 && payload.length) {
      try { obj = JSON.parse(payload.toString('utf8')); } catch {}
    }
    return { code, event, isLast, seq, payload: obj };
  } else if (msgType === 15) { // SERVER_ERROR_RESPONSE
    code = msg.readInt32BE(off); off += 4;
    const payloadSize = msg.readUInt32BE(off); off += 4;
    let payload = msg.subarray(off, off + payloadSize);
    if (comp === 1) { try { payload = zlib.gunzipSync(payload); } catch {} }
    let obj = null;
    if (ser === 1 && payload.length) {
      try { obj = JSON.parse(payload.toString('utf8')); } catch {}
    }
    return { code, event, isLast, seq, error: obj };
  }
  return { code: 0, event, isLast, seq };
}

// ---- WAV parsing ----
function readWavInfo(buf) {
  if (buf.length < 44) throw new Error('WAV too short');
  const channels = buf.readUInt16LE(22);
  const sampleRate = buf.readUInt32LE(24);
  const bitsPerSample = buf.readUInt16LE(34);
  let pos = 36;
  while (pos < buf.length - 8) {
    const id = buf.subarray(pos, pos + 4);
    const sz = buf.readUInt32LE(pos + 4);
    if (id.toString('ascii') === 'data') {
      const data = buf.subarray(pos + 8, pos + 8 + sz);
      const sampleWidth = bitsPerSample / 8;
      const frames = Math.floor(data.length / (channels * sampleWidth));
      return { channels, sampleRate, sampleWidth, frames, data };
    }
    pos += 8 + sz;
  }
  throw new Error('No data subchunk in WAV');
}

// ---- Main ----
async function main() {
  const args = process.argv.slice(2);
  const wavPath = args.find(a => !a.startsWith('--'));
  if (!wavPath || !fs.existsSync(wavPath)) {
    console.error('Usage: node sauc_asr_cli.js <wav_file> [--resource <id>] [--key <api_key>] [--out <path>]');
    process.exit(1);
  }

  // Load API key
  let apiKey = proces…EY || '';
  const keyArg = args.indexOf('--key');
  if (keyArg >= 0 && args[keyArg + 1]) apiKey = args[keyArg + 1];

  let resource = 'volc.bigasr.sauc.duration';
  const resArg = args.indexOf('--resource');
  if (resArg >= 0 && args[resArg + 1]) resource = args[resArg + 1];

  const wav = fs.readFileSync(wavPath);
  const { channels, sampleRate, sampleWidth, frames, data } = readWavInfo(wav);
  const pcm = data.subarray(0, frames * channels * sampleWidth);
  const totalMs = Math.floor((pcm.length / (channels * sampleWidth)) / sampleRate * 1000);
  const segMs = 200;
  const bytesPerMs = (sampleRate * channels * sampleWidth) / 1000;
  const segSize = Math.max(1, Math.floor(bytesPerMs * segMs));
  const segCount = Math.ceil(pcm.length / segSize);

  console.error(`ASR: ${wavPath} | ${sampleRate}Hz ${channels}ch ${sampleWidth*8}bit | ${totalMs}ms | ${segCount} x ${segMs}ms segments`);

  const payload = {
    user: { uid: 'openclaw_agent' },
    audio: { format: 'raw', codec: 'pcm_s16le', rate: sampleRate, bits: sampleWidth * 8, channel: channels },
    request: {
      model_name: 'bigmodel',
      enable_itn: true,
      enable_punc: true,
      enable_ddc: true,
      show_utterances: true,
      enable_nonstream: true,
    },
  };

  const ws = new Ws('wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream', {
    'X-Api-Key': apiKey,
    'X-Api-Resource-Id': resource,
    'X-Api-Connect-Id': Math.random().toString(36).slice(2, 12),
  });

  const results = [];
  let seq = 1;

  await new Promise((resolve, reject) => {
    ws.on('error', (e) => {
      console.error('WS error:', e.message || e);
      reject(e);
    });
    ws.on('close', () => {
      console.error('WS closed');
    });
    ws.on('open', async () => {
      try {
        // 1) Send full client request
        const fullReq = buildFullClientRequest(seq, payload);
        seq++;
        ws.sendBinary(fullReq);
        console.error('Sent FULL_CLIENT_REQUEST');

        // 2) Send audio segments at natural rate (200ms intervals)
        let segIdx = 0;
        const sendInterval = setInterval(() => {
          if (segIdx >= segCount) {
            clearInterval(sendInterval);
            // Send final marker? The last packet has NEG_WITH_SEQ flag
            return;
          }
          const start = segIdx * segSize;
          const end = Math.min(start + segSize, pcm.length);
          const isLast = (segIdx === segCount - 1);
          const pkt = buildAudioOnlyRequest(seq, pcm.subarray(start, end), isLast);
          ws.sendBinary(pkt);
          if (!isLast) seq++;
          segIdx++;
          if (isLast) {
            clearInterval(sendInterval);
            console.error('All segments sent. Waiting for results...');
          }
        }, segMs);

        // 3) Receive responses
        ws.on('data', (frame) => {
          // WebSocket frame -> SAUC binary message
          const resp = parseServerResponse(frame);
          if (resp.error) {
            console.error('Server error:', JSON.stringify(resp.error));
            results.push({ error: resp.error });
            return;
          }
          if (resp.payload) {
            results.push(resp.payload);
            const utt = resp.payload.result?.text || resp.payload.utterances?.map(u => u.text).join('');
            if (resp.payload.result) console.error('  ...', JSON.stringify(resp.payload.result).slice(0, 200));
          }
          if (resp.isLast) {
            clearInterval(sendInterval);
            ws.close();
            resolve();
          }
        });
      } catch (e) { reject(e); }
    });
    ws.connect();
    setTimeout(() => {
      ws.close();
      resolve();
    }, 60000); // 60s hard timeout
  });

  // Aggregate results
  const text = results
    .map(r => r.result?.text || r.utterances?.map(u => u.text || '').join('') || '')
    .filter(Boolean)
    .join('');

  const out = {
    success: !!text,
    text: text,
    file: wavPath,
    duration_ms: totalMs,
    resource,
  };

  const outArg = args.indexOf('--out');
  if (outArg >= 0 && args[outArg + 1]) {
    fs.writeFileSync(args[outArg + 1], JSON.stringify(out, null, 2));
    console.log(JSON.stringify({ success: out.success, text, file: outArg + 1 }));
  } else {
    console.log(JSON.stringify(out));
  }

  if (!text) {
    console.error('No transcription produced. Raw results:', JSON.stringify(results).slice(0, 500));
    process.exit(1);
  }
}

main().catch((e) => {
  console.error('FATAL:', e.message || e);
  process.exit(1);
});
