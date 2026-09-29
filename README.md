# 豆包语音双向工具包 (doubao-voice)

文字转语音 (TTS) 与 语音转文字 (ASR) 一体化方案，基于火山引擎（豆包）语音网关
`openspeech.bytedance.com` 的双向流式 WebSocket 协议。

| 模块 | 协议端点 | 依赖 |
|------|----------|------|
| `tts/` | `wss://.../api/v3/tts/bidirection` | Python 3.10+ · `websockets` |
| `asr/` | `wss://.../api/v3/sauc/bigmodel_async` | Python + `websockets` + `ffmpeg`；或 Node.js 零依赖版 |

```
doubao-voice/
├── SETUP.md            # ① 先读这个：申请豆包/火山 API Key + 资源配置（含常见报错排查）
├── USAGE.md            # ② 逐步使用指南（供人类和 AI 都读）
├── README.md           # 本文件
├── tts/
│   ├── protocols.py    # 火山双向流式 TTS 协议实现（二进制帧打包/解析）
│   ├── tts_demo.py     # 核心合成逻辑（synthesize_text），含默认参数
│   ├── tts_cli.py      # 命令行入口：stdout 输出 JSON，机器友好
│   ├── run_tts.py      # 便捷入口（透传参数给 tts_cli.py）
│   └── requirements.txt
└── asr/
    ├── sauc_asr_cli.py   # 核心 ASR 客户端（SAUC 2.0 双向流式，固定节拍打包）
    ├── sauc_asr_cli.js   # 零依赖 Node 版（仅需 16k WAV 输入）
    ├── run_asr.py        # 便捷入口（自动 ffmpeg 转 16k WAV）
    ├── run_asr_js.js     # 便捷入口（Node 版）
    ├── probe_asr_key.py  # 验证 API Key 可用性（零依赖，手搓 WS 握手）
    ├── probe_asr_key.js  # 同上，Node 版
    └── sauc_asr_probe.py # 旧版握手探针（保留兼容）
```

## 三步走

**① 申请 Key** — 打开 `SETUP.md`，跟着做：注册火山引擎 → 开通语音服务 → 创建 API Key → 用 `probe_asr_key.py` 验证。

**② 配置 Key** — 推荐环境变量（Windows）：

```powershell
[Environment]::SetEnvironmentVariable("DOUBAO_API_KEY", "你的KEY", "User")
```

**③ 使用** — 打开 `USAGE.md`，按场景操作。

## 设计约定（AI 调用者注意）

- **stdout 只输出最终结果**：TTS 输出一行 JSON（`output_file` 指向音频）；ASR 输出转写纯文本（可选 `--out` 写 JSON）
- **日志全部走 stderr**，可加 `--quiet` 静默
- **退出码**：`0` 成功 · `1` 失败 · ASR 无结果 `2`
- 这样 OpenClaw 等 Agent 直接 `subprocess` 捕获 stdout 就能拿到结果

## 安全提醒

- 仓库不包含任何 API Key。提交前请再次确认 `git diff --cached` 里没有误提交
- `.gitignore` 已排除 `output/`、`__pycache__/` 等产物
