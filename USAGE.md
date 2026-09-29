# 使用指南（人类 / AI 皆可读）

> 按顺序做：先 `SETUP.md` 申请 Key 并验证，再回来这里操作。
> 如果你是 AI Agent：直接读每个脚本的模块 docstring（`--help` / `py xxx.py` 无参数打印用法），
> 按下方"Agent 调用约定"执行即可。

## 环境

| 平台 | 说明 |
|------|------|
| Windows | `py` 命令（或 `python`）；PowerShell 设环境变量用 `[Environment]::SetEnvironmentVariable(...)` |
| Linux/macOS | `python3`；`export DOUBAO_API_KEY=...` |
| Node 零依赖版 | 需要 Node 18+（内置 WebSocket 实现自封装，无需 npm） |

依赖安装（Python 版，一次即可）：

```powershell
cd tts
py -m pip install -r requirements.txt   # 仅 websockets
```

ASR 的 Python 版还需要 `ffmpeg` 在 PATH 中（用于把 mp3 等转成 16kHz WAV）。
没有 ffmpeg 时可用 Node 零依赖版（`run_asr_js.js`），但输入必须是 WAV。

## 配置 Key（二选一）

```powershell
# A. 环境变量（推荐，可持久化）
[Environment]::SetEnvironmentVariable("DOUBAO_API_KEY", "你的KEY", "User")

# B. 每次传参（临时）
py run_tts.py "你好" --api-key 你的KEY
```

取 Key 优先级：**`--key/--api-key` 参数 > 环境变量 `DOUBAO_API_KEY` > `openclaw.json`（仅 ASR Python 版自动读）**。

---

## TTS：文字 → 语音（mp3）

### 一行合成

```powershell
cd tts
py run_tts.py "你好，我是豆包语音"
```

成功输出（stdout 一行 JSON）：

```json
{"success": true, "output_file": "...\\output\\tts_20260929_xxx_zh_female_vv_uranus_bigtts.mp3", "audio_size": 9837, "text_length": 8, "billed_chars": 8, "speaker": "zh_female_vv_uranus_bigtts", "format": "mp3"}
```

音频落在 `tts/output/`（自动创建、时间戳命名、不覆盖历史文件）。

### 常用选项

```powershell
# 换音色（控制台"音色库"查 ID）
py run_tts.py "测试" --speaker zh_male_dd_neural_bigp

# 指定输出文件
py run_tts.py "测试" --output D:/audio/hello.mp3

# 从文件读文本
py run_tts.py --file script.txt

# 调语速/音量/音调
py run_tts.py "测试" --speech-rate 50 --loudness-rate 20 --pitch -3

# 字级时间戳（字幕）
py run_tts.py "测试" --subtitle

# 完整参数表
py tts_cli.py --help
```

### 默认参数速查

| 参数 | 默认 | 说明 |
|------|------|------|
| 音色 | `zh_female_vv_uranus_bigtts` | vv 女声 |
| 格式 | `mp3` | 可选 `mp3/pcm/ogg_opus/wav` |
| 采样率 | `24000` | 8000/16000/22050/24000/32000/44100/48000 |
| 资源 | `seed-tts-2.0` | `--resource-id seed-icl-2.0` 可切声音复刻 |

### 常见问题（TTS）

| 现象 | 原因 | 处理 |
|------|------|------|
| 401 鉴权失败 | Key 错/没设 | 见 `SETUP.md` 第 3 节验证 Key |
| 403 resource not granted | 该资源未授权 | 控制台给 Key 授权 `seed-tts-2.0` |
| 未收到音频 | 文本为空或网络中断 | 确认文本非空，换网络重试 |

---

## ASR：语音 → 文字

### 一行转写（Python 版，支持 mp3 等任意 ffmpeg 可读格式）

```powershell
cd asr
py run_asr.py audio.mp3
```

stdout 只输出转写文本（一行）；进度日志在 stderr，加 `--quiet` 可静默。

### 可选参数

```powershell
# 输出结构化 JSON（stdout 仍是纯文本，--out 额外写文件）
py run_asr.py audio.mp3 --out result.json

# 显式传 Key
py run_asr.py audio.mp3 --key 你的KEY

# 指定 ffmpeg 路径
py run_asr.py audio.mp3 --ffmpeg D:/tools/ffmpeg.exe
```

### Node 零依赖版（输入需为 16k WAV）

```powershell
# 先把其他格式转成 16kHz/16bit/单声道 WAV
ffmpeg -i in.mp3 -acodec pcm_s16le -ar 16000 -ac 1 out.wav

node run_asr_js.js out.wav
```

### 验证 Key（零依赖，推荐先跑）

```powershell
cd asr
py probe_asr_key.py            # 读 DOUBAO_API_KEY
py probe_asr_key.py 你的KEY
# 或 Node 版
node probe_asr_key.js 你的KEY
```

输出 `OK: websocket handshake accepted (101)` 即 Key+资源可用。

### 协议要点（给需要复用的 AI）

- 端点：`wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async`，头 `X-Api-Key` + `X-Api-Resource-Id`
- 音频分包：16kHz/16bit/单声道 WAV 按 200ms 定长切，**固定节拍发送**（后台 sender + 前台 drain，勿阻塞式逐包等回包，否则触发 8s 超时）
- 回包是**累积式** partial（每次 partial 含全部已识别文本），取最长的一条作为最终结果，**不要拼接所有 partial**
- 最后一包标记 `is_last`，收到即结束
- 帧封装：4 字节头（`0x11` 开头）+ gzip 压缩 payload，详见 `sauc_asr_cli.py` 的 `build_audio/parse_response`

---

## Agent 调用约定

1. 先 `probe_asr_key`（或 TTS 一次性合成）确认 Key 可用，再跑正式任务
2. 捕获 **stdout** 作为结果；日志看 **stderr**（或加 `--quiet`）
3. 退出码：`0` 成功 · `1` 失败 · ASR 无结果 `2`
4. 产物路径在结果里（TTS 的 `output_file` / ASR 的 `--out`），**不要假设文件名**
5. 失败时按 `SETUP.md` 第 3 节的报错对照表排查，不要盲目重试

## 发布到 GitHub 前检查清单

- [ ] `git grep -n "4406b8bd\|b5527935"` 应无结果（本包已剔除两个历史 Key）
- [ ] 不要把 `output/`、`__pycache__/` 提交（`.gitignore` 已覆盖）
- [ ] README/SETUP/USAGE 里无本机绝对路径残留（如 `E:\develop\...` 已用占位符）
