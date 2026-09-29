#!/usr/bin/env node
/**
 * 零依赖验证豆包/火山语音 API Key 是否能连通 SAUC ASR 端点
 * (只用 node 内置 https/socket 手搓 WS 握手, 无需 npm install)
 *
 * 用法:
 *   node probe_asr_key.js <apiKey> [resourceId]
 *   node probe_asr_key.js                    # 读环境变量 DOUBAO_API_KEY
 * 退出码: 0=握手成功(101)  1=被拒绝  3=没有key  4=网络错误
 */
import https from 'node:https';
import process from 'node:process';
import crypto from 'node:crypto';

const HOST = 'openspeech.bytedance.com';
const PATH = '/api/v3/sauc/bigmodel_async';
const DEFAULT_RESOURCE = 'volc.seedasr.sauc.duration';

const args = process.argv.slice(2);
const key = args[0] && !args[0].startsWith('--') ? args[0] : process.env.DOUBAO_API_KEY || '';
const resource = args[1] || DEFAULT_RESOURCE;

if (!key) {
  console.error('Usage: node probe_asr_key.js <apiKey> [resourceId]');
  console.error('(or set env DOUBAO_API_KEY first)');
  process.exit(3);
}

const wsKey = crypto.randomBytes(16).toString('base64');
const req = https.get({
  host: HOST,
  port: 443,
  path: PATH,
  headers: {
    'Upgrade': 'websocket',
    'Connection': 'Upgrade',
    'Sec-WebSocket-Key': wsKey,
    'Sec-WebSocket-Version': '13',
    'X-Api-Key': key,
    'X-Api-Resource-Id': resource,
  },
});

req.setTimeout(15000, () => {
  console.error('TIMEOUT');
  req.destroy();
  process.exit(4);
});

req.on('upgrade', (res) => {
  const status = res.statusCode;
  console.log(`resource: ${resource}`);
  console.log(`status:   ${res.headers['x-status-code'] || status}`);
  if (status !== 101) {
    console.error('=> 握手被拒绝');
    process.exit(1);
  }
  console.log('OK: websocket handshake accepted (101) — key 可用');
  res.socket.destroy();
  process.exit(0);
});

req.on('response', (res) => {
  // 非 upgrade 的 HTTP 响应 (401/403 会以普通 response 形式返回)
  let body = '';
  res.on('data', (c) => (body += c));
  res.on('end', () => {
    console.log(`resource: ${resource}`);
    console.log(`status:   ${res.statusCode}`);
    if (body) console.log('body: ' + body.slice(0, 500));
    if (res.statusCode === 401) console.error('=> 401: API Key 无效');
    if (res.statusCode === 403) console.error('=> 403: 资源未授权给该 Key');
    process.exit(1);
  });
});

req.on('error', (e) => {
  console.error('CONNECT_ERR: ' + e.message);
  process.exit(4);
});
