#!/usr/bin/env node
// 运行 ASR 语音转文字的零依赖 Node 入口 — 调用同目录的 sauc_asr_cli.js
// 无需 npm install。
//
// 用法:
//   node run_asr_js.js <音频文件.wav> [选项]      # 选项透传给 sauc_asr_cli.js
//   node run_asr_js.js audio.wav
//   node run_asr_js.js audio.wav --out result.json
//   node run_asr_js.js audio.wav --key 你的KEY
//
// 注意: 零依赖 JS 版要求输入是 WAV (或能被其内置解析处理的格式)。
// 其他格式 (mp3/m4a/ogg) 请先用 ffmpeg 转成 WAV:
//   ffmpeg -i in.mp3 -acodec pcm_s16le -ar 16000 -ac 1 out.wav

const { spawn } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');

const script = path.join(__dirname, 'sauc_asr_cli.js');
if (!fs.existsSync(script)) {
  console.error('找不到 ' + script);
  process.exit(1);
}

const child = spawn(process.execPath, [script, ...process.argv.slice(2)], { stdio: 'inherit' });
child.on('close', code => process.exit(code ?? 1));
