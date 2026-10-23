# GPT-Live-1 / Echo 原声实验

2026-10-24 核对官方 OpenAI documentation。当前分支实现的是 **GPT-Live-1 → Echo 原声播放**，保留既有 Gemini / MeanVC2 路径。协议测试通过不代表本机账号已能调用模型；没有成功 API 会话时不能宣称已听到 Echo 或已验证全双工体验。

## 与原方案的区别

`gpt-live-1` 是官方可同时听说的模型，使用独立 Live API；不是 `gpt-realtime` 的别名。`echo` 是其支持的音色。模型语音对话可以持续进行，把复杂推理/工具委派给另一个后台。此次原声实验没有外部任务后台，若收到委派请求会明确终止，不伪造工具结果。[模型](https://developers.openai.com/api/docs/models/gpt-live-1)、[官方协议](https://developers.openai.com/api/reference/resources/live/primary-websocket)

## 连接与音频

服务端连接 `wss://api.openai.com/v1/live/sessions`，`Authorization: Bearer <API key>`。地址不带 model query。第一条消息：

```json
{
  "type": "session.start",
  "session": {
    "model": "gpt-live-1",
    "instructions": "Speak natural Japanese. Keep replies concise.",
    "audio": {
      "format": {"type": "audio/pcm", "rate": 16000},
      "output": {"voice": "echo"}
    },
    "delegation": {"type": "client"}
  }
}
```

等 `session.started`，核验 resolved model、所选voice、format 才向网页发 ready。默认Echo，另允许官方日语声音Shida、Harema；详见[原生声音方案](native-voice-options.md)。Voice 和 format 在 session 中不可更改。Live 支持16k/24k PCM；本实现固定 **16kHz、mono、signed PCM16 little endian** 输入输出，原样传递、无重采样/变声。

- 输入：`session.input_audio.append`，字段 `audio` 为 raw PCM base64，无 WAV 头。
- 输出：`session.output_audio.delta`，字段 `delta` 为同格式 PCM base64。
- 收发并发，持续转发麦克风和静音。网页按真实时间提供输入；不能瞬间推完整文件来冒充实时。
- 官方不限制固定帧长，要求完整sample、连续有序。此网页额外限制每个输入包≤100ms，以控制队列。
- 输出使用单个 session token 和递增 sequence。适配器待投递音频队列上限为2秒或16包，超限会失败退出；浏览器另有12秒播放积压上限，两者不能当作实际延迟。

[WebSocket连接/音频规则](https://developers.openai.com/api/docs/guides/voice-websockets?api=live)

## 连续流与打断边界

Live 主 WebSocket 没有 audio-done、turn-complete 或输出音频时间戳。不能套用 Gemini 的句末 flush；`response.create` 是委派后台工作，不是普通语音回答的启动按钮。用户和模型字幕独立累加，字幕间隔不证明任何一方已说完。[会话管理](https://developers.openai.com/api/docs/guides/live-conversations#manage-speech-and-transcripts)

当前适配器没有伪造自动/手动打断事件，也不把文字包装为用户语音。网页持续发送麦克风音频，模型根据听到的语音决定是否让话。提示词要求被打断时停下听对方说。**这项能力仍需真实账号下的重叠讲话试听；mock只能证明传输不因输出而停收。**

未来接 MeanVC2 时必须另外验证保持状态的持续处理、缓冲清理和声音相似度，不能因为原声路径正常就宣称变声后也正常。[打断提示](https://developers.openai.com/api/docs/guides/live-prompting#interruptions)、[应用播放控制](https://developers.openai.com/api/docs/guides/voice-server-controls?api=live#check-speech-before-playback)

## 结束、故障与访问

应用显式 `stop` 或取消运行任务才触发 `session.close`，有界等待 `session.closed`；没有最终确认会报告失败。输入流EOF不会结束Live会话；本地 `audio_stream_end` 仅表示输入暂停，不发送任何伪造的轮次完成事件。有限音频文件应按实际时间追加静音，或显式结束整个会话。关闭期间取消收发/播放任务，清掉本会话音频。供应商报错可能含 URL 或请求内容，因此仅返回固定、无凭据错误消息。

官方账号需要 GPT-Live 权限；Free tier不支持。官方模型单价$0.05/分钟，按秒计费，后台模型/工具另计费。具体当前账号权限必须实际验证。这里没有密钥、凭据或探测成功声明。[模型限制与价格](https://developers.openai.com/api/docs/models/gpt-live-1)

WebRTC是官方推荐的媒体方式：服务端 POST `/v1/live/sessions` 携带 `session` 与 `transport:{type:"webrtc",sdp:...}`，浏览器应用SDP answer，等 `session.started`；不再发送session.start。此分支用WebSocket是为了复用现有本地音频中继，未实现WebRTC视频/嘴型。[WebRTC](https://developers.openai.com/api/docs/guides/voice-webrtc?api=live)

## 本地协议测试

```sh
python -m unittest discover -s work/ivan-jp-pilot/live-source -p test_openai_live.py -v
```

测试使用loopback aiohttp假服务端及假key，覆盖resolved配置、连续收发、静音保留、PCM错误、队列上限、关闭/取消、错误脱敏和不支持的委派请求。它们不接OpenAI，不计费，也不评价音质。
