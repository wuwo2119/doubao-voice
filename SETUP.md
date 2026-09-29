# 申请豆包（火山引擎）语音 API Key

本包内的 TTS 与 ASR 均使用火山引擎语音网关 `openspeech.bytedance.com`，鉴权方式为在 WebSocket 请求头里携带：

- `X-Api-Key`：你的 API Key（控制台创建）
- `X-Api-Resource-Id`：资源/模型 ID（必须选择已开通服务的版本，未开通会报 403 "resource not granted"）

## 1. 注册与开通

1. 注册/登录火山引擎：https://www.volcengine.com/（需实名认证）
2. 语音技术产品页（双向流式 TTS/ASR 属于语音技术）：
   https://www.volcengine.com/product/speech
3. 开通"语音合成"（TTS）与"语音识别"（ASR）服务。控制台会列出需要授权的资源版本：
   - TTS：`seed-tts-2.0`（普通合成）、`seed-icl-2.0`（声音复刻）
   - ASR：`volc.seedasr.sauc.duration`（即 `volc.bigasr.sauc.duration` 的 BigModel SAUC 2.0 资源 ID，包内默认用这个）
4. 新注册用户通常有免费额度，具体以控制台价格页为准。

## 2. 创建 API Key

1. 控制台右上角进入「密钥管理 / API Key 管理」：
   https://console.volcengine.com/iam/key-management/
   （语音产品页内也有"API 管理"入口，跳转到同一处）
2. 点击「新建密钥」，填写备注（建议写 "doubao-tts-asr" 方便识别）
3. 创建后**立即复制保存** API Key——只显示一次，丢了只能删除重建
4. 可选：为 Key 添加 IP 白名单限制

## 3. 验证 Key 可用

```powershell
# 方式一：Python 版（先装依赖）
cd asr
py -m pip install websockets
py probe_asr_key.py --key 你的KEY

# 方式二：零依赖 Node 版
node probe_asr_key.js 你的KEY
```

预期输出：`OK: websocket handshake accepted (101)`

也可以顺手验证 TTS Key（能连通并合成一小段才算通）：

```powershell
cd tts
py -m pip install websockets
py run_tts.py "验证通过" --api-key 你的KEY --quiet
```

成功时 stdout 输出一行 JSON，`output_file` 指向 `output/` 下的 mp3。

### 常见报错区分

| 报错 | 含义 | 处理 |
|------|------|------|
| 401 "key doesn't exist" | Key 无效/拼错 | 重新复制 |
| 403 "resource not granted" | Key 有效，但该资源未授权给这个 Key | 在控制台「API 管理」里给该资源添加授权（选择对应的 `X-Api-Resource-Id`），或确认资源版本拼写正确 |
| 连接超时 | 网络/防火墙 | 检查能否访问 `openspeech.bytedance.com:443` |

> 注意：TTS 与 ASR 的授权是**按资源分开**的。`seed-tts-2.0` 开通了不代表 `volc.seedasr.sauc.duration` 也开通了，需要分别确认。

## 4. 把 Key 写进本包（环境变量，推荐）

本包所有脚本的取 Key 优先级：**命令行参数 > 环境变量 > 默认值（空）**。推荐用环境变量，不要把 Key 写进代码提交到 GitHub：

### Windows PowerShell（当前会话）

```powershell
$env:DOUBAO_API_KEY = "你的KEY"
```

### Windows 持久化（用户级）

```powershell
[Environment]::SetEnvironmentVariable("DOUBAO_API_KEY", "你的KEY", "User")
```

### 跨平台（bash / zsh）

```bash
export DOUBAO_API_KEY="你的KEY"        # 当前会话
# 或写入 ~/.bashrc / ~/.zshrc 持久化
```

### 每个脚本单独传参（临时）

```powershell
# TTS
py tts\run_tts.py "你好" --api-key 你的KEY

# ASR（Python 版）
py asr\run_asr.py audio.mp3 --key 你的KEY

# ASR（Node 版，零依赖）
node asr\run_asr_js.js audio.mp3 --key 你的KEY
```

## 5. （可选）OpenClaw 用户：配置成 OpenClaw 的 doubao provider

如果你是在 OpenClaw 里用这套工具，可以让 OpenClaw 直接管理 Key（推荐用 SecretRef，不写明文）：

```powershell
openclaw config set models.providers.doubao.apiKey @DOUBAO_API_KEY
```

或写进 `~/.openclaw/openclaw.json`：

```json
{
  "models": {
    "providers": {
      "doubao": {
        "apiKey": "你的KEY"
      }
    }
  }
}
```

本包内的 ASR Python 版会**自动读取** `~/.openclaw/openclaw.json` 里 `models.providers.doubao.apiKey`（支持字符串和 SecretRef 两种形式），所以 OpenClaw 配好后连 `--key` 都不用传。

## 6. 可用资源 ID 速查

| 能力 | X-Api-Resource-Id | WebSocket 端点 |
|------|-------------------|----------------|
| TTS 双向流式 | `seed-tts-2.0` | `wss://openspeech.bytedance.com/api/v3/tts/bidirection` |
| TTS 声音复刻 | `seed-icl-2.0` | 同上 |
| ASR 大模型流式 | `volc.seedasr.sauc.duration` | `wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async` |

> 音色 ID（如 `zh_female_vv_uranus_bigtts`）在控制台「音色库」里查，TTS 脚本可通过 `--speaker` 指定。
