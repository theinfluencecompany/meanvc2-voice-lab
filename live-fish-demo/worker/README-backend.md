# Fish 实机试听后端

这是独立公司 Demo Worker `demo-voice-lab` 的后端，部署目标为 <https://demo-voice-lab.zjudn2013.workers.dev/>。不需要本地 CPU，不经过 MeanVC2，不写 Prelulu 的角色或通话配置。

当前页面只做Fish原文朗读。45个角色／语言／模式标准句由已授权的Prelulu试听接口生成，作为缓存音频随静态资源部署；文本与白名单标准句完全匹配时，即使没有Fish直连密钥也可公开试听，响应明确为`source: sample_cache`，不提供本次生成耗时。任何不同的新句都不会偷偷替换为标准句。动态合成配置后响应为`source: live`并继续要求体验码。

## 配置

以下配置只放在此 Demo Worker's secrets，不能提交到仓库或前端：

- `FISH_API_KEY`：专用 Fish 测试密钥。没有密钥时实时试听返回 503。
- `DEMO_ACCESS_TOKEN`：付费接口的体验码，前端通过 `Authorization: Bearer ...` 请求头发送；不要放进公共 JS。
- `GEMINI_API_KEY`：可选，对话文字生成所需。未配置时仅开放直接输入台词试听。
- `GEMINI_MODEL`：可选，默认 `gemini-2.5-flash-lite`。三种发声模式共用此文本模型。默认值并不代表当前账号已实际验证拥有调用权限。

Worker 现有 secret 配置随部署保留。使用获授权的公司 Demo 凭据显式部署本目录 `wrangler.jsonc`；不使用生产 Worker 凭据，也不复制生产密钥。`../dist` 由网页构建产生。

## 接口

- `GET /api/config`：角色、语言、发声模式、价格参考，以及 `capabilities.tts/chat/transcription`。能力值只表示密钥已配置，不是提供商可用性探针；语音识别固定为 false。
- `GET /healthz`：Worker 本身的健康状态。
- `POST /api/synthesize`：`{ characterId, locale, mode, text }`。
- `POST /api/chat`：同上，并可有 `history: [{role: "user" | "assistant", content}]`，最多 12 条。`text` 是本轮输入；响应 `text` 为本轮真正送给 Fish 的回复，`inputText` 保留用户输入。

付费 POST 必须携带正确的 `Authorization: Bearer ...`，并且 `Origin` 必须与请求 URL 同源。本地预览也必须走同源页面。模型和 voice ID 只能从共享目录中选择；客户端无法指定任意模型或参考音色。ASMR 固定为 `drama-3` 和服务端 `[whisper]` 标签，其余两种模式不加情绪标签。客户端台词禁止方括号、尖括号和控制字符，避免注入 Fish 指令。没有改变参考音色的语言限制设置。

响应为整段 MP3 的 `audioDataUrl`，最多 3 MiB；`totalMs` 包含此次完整生成等待，**不是首音延迟或通话延迟**。`requestedModel` 是请求的模型，`servedModel: null` 表示提供商未证明实际服务型号。不能把它称为真正全双工或与生产相同的角色人格；聊天使用此 demo 的简短独立角色描述。

安全边界：输入 JSON 在解析前限制为 24 KiB，台词最多 1000 字符 / 4096 UTF-8 字节。Fish 请求 45 秒超时，Gemini 请求 30 秒超时，并响应客户端取消。不给前端返回提供商错误原文，也不记录请求台词或密钥。每个 isolate 每 IP 每分钟最多 12 次生成、全 isolate 最多两次并行生成，认证尝试另有上限。**isolate 限流不是跨区域全局支出上限**；此体验码版只用于小范围试用，扩大分享前需要 Durable Object/平台限流和提供商预算限制。

运行针对接口边界和供应商请求的单元检查：

```sh
node --test live-fish-demo/worker/worker.test.mjs
```
