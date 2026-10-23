# 下一步：先听原生声音，再决定是否需要变声

2026-10-24 已搜索并读取官方 OpenAI documentation。本文件是待验证方案；没有创建或上传音色，没有 API 调用，也没有替换用户指定的 Echo。

**建议顺序：Echo → Shida 日语男声基准 → 按文字创建角色新音色。** 先确认原生全双工对话的声音、日语发音和交互可接受，再决定是否继续用 MeanVC2 保留旧角色音色。录音克隆暂不作为本轮默认路线。

## 三种声音来源不能混为一谈

| 方式 | 实际得到什么 | 当前可用条件 | 对本项目的用途 |
|---|---|---|---|
| 内置 Echo / Shida / Harema | 官方现成音色 | 可调用 GPT-Live 的 API 账号 | 最快做原生对照，不新增变声阶段 |
| Prompt-to-voice | 根据音高、音质、年龄感等文字描述创建一个新音色 | 已向所有 API 客户开放；具体账号调用权限仍待验证 | 为角色重新选/设计声音，不是克隆 Fish |
| 录音创建 custom voice | 模型尝试贴近指定参考录音 | 仅获批客户；同一真人的同意录音和参考录音 | 有正式录音与权限后再评估保留既有声音 |

2026-10-16 官方开放了 prompt-to-voice，并新增日语自然音色 **Shida（男性）**、**Harema（女性）**。[更新记录](https://developers.openai.com/api/docs/changelog)、[声音列表](https://developers.openai.com/api/docs/guides/live-conversations#voice-options)

## 第一次有访问权限时怎么做

1. **先按用户选择测试 Echo。** 保存原声并实际进行几轮日语对话和重叠讲话。当前 adapter 固定 Echo；不要悄悄换别的声音后仍标 Echo。
2. 下一组另建 session，将 `session.audio.output.voice` 改成字符串 `"shida"`。女角色对照用 `"harema"`。使用相同输入台词、人物指令和麦克风条件；先听原生声音，再与官网当前样音对比。
3. 如果原生自然度可接受，但现成声线不适合角色，才创建 **1个**文字定制男声。保留新 voice ID，随后固定这个 ID 比较日语/英语及多轮对话，避免每次重建导致声音变化。

上述选择均在启动 session 时设置；通话中换音色需要新 session。官方要求对实际目标语言、发音和地区风格做试听，没有保证某个 voice 在所有语言下都保持同等自然度或身份相似度。[会话设置](https://developers.openai.com/api/docs/guides/live-conversations#configure-a-session)、[语言提示](https://developers.openai.com/api/docs/guides/live-prompting#language-and-pronunciation)

## 文字定制：具体请求

POST `https://api.openai.com/v1/audio/voices`，服务端 API key认证，JSON（这是待执行示例）：

```json
{
  "type": "prompt",
  "name": "JP adult male neutral pilot",
  "model": "auto",
  "prompt": "An adult masculine voice with a medium-low pitch, clear smooth timbre, moderate vocal effort and an even conversational pace. Natural standard Japanese pronunciation. Little breathiness; no whispering, exaggerated rasp or theatrical growling. Warm and composed without sounding forceful.",
  "script_hint": "こんにちは。今日も会えてうれしいです。今日はどんな一日でしたか。よければ、少しお話ししましょう。"
}
```

这是声学特征描述，不应写“模仿 Ivan/Fish/某个具名人物”。成功响应含 `id` 等元数据，**不含音频预览**。试听需新开 GPT-Live session：

```json
{
  "model": "gpt-live-1",
  "instructions": "[人物对话规则] [保留创建时的声音描述] この通話は自然な日本語で話してください。",
  "audio": {"output": {"voice": {"id": "voice_RETURNED_ID"}}}
}
```

创建免费；试听/通话按普通 Live session计费。每次创建保存一个新voice，组织上限100个。文字生成的voice仅用于Live，不能直接拿去Realtime或`/audio/speech`。上述日语风格只是请求目标，成功创建不等于已经达到听感要求。[创建API](https://developers.openai.com/api/reference/resources/audio/subresources/voices/methods/create)、[Prompt-to-voice指南](https://developers.openai.com/api/docs/guides/custom-voices#create-a-voice-from-a-description)

## 录音克隆为什么不能现在就承诺

官方录音路线需要同意录音和参考样音来自同一人，项目具有相应voice读写和custom-voice权限。建议样音10–60秒，至少5秒实际说话、15个转写tokens。`gpt-live-1` 对这一路线的文档只明确支持英语口音，没有给出“用Fish现成角色样音即可跨语言精确复现”的保证。不能把下载的Fish试听自动当作合格的同意录音。[录音声音与Live](https://developers.openai.com/api/docs/guides/custom-voices#use-a-voice-from-a-recording-with-gpt-live)

**技术判断：原生定制voice能去掉外部变声阶段，因此值得优先比较；它是否更好听、是否像原角色、日英是否稳定，仍需实际听。** 如果目标是完整保留原版Ivan身份，选Shida或文字生成新声音都属于重新选角，不能宣称已解决旧声音克隆。

Live官方价$0.05/分钟、按秒计费，后台模型/工具另计费，Free tier不支持；当前账号可用性未验证。[模型价格与限制](https://developers.openai.com/api/docs/models/gpt-live-1)
