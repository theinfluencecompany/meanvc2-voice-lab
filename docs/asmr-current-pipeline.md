# 现有语音方案：从耳语试听到角色晚安

本分支探索现有文字回复 → TTS 路线。原生 Live / GPT Live 由另一个工作分支负责。本文件提出产品候选，不代表已批准或部署到 Prelulu。

## 先把比较做对

- 固定角色音色、台词、语言、语气，只切模型，比较 S2 Pro / S2.1 Pro / Drama 3。
- 固定音色、台词、语言、模型，只切正常 / 耳语，比较发声方式。
- Fish 和 Eleven 的 voice ID 不互通。Eleven 内置 Roger / Sarah 的结果单独标记；不同声音间不能直接得出“更像 Ivan”的结论。
- 让试听者分别评价角色相似度、目标语言自然度、耳语舒适度，完整生成耗时单列。耳语标签不保证双耳 ASMR、距离感或触发音。

## 建议的最小产品功能

**角色晚安语音**：角色针对一条用户消息给出简短回应，用户选择正常或耳语，生成可重播的语音消息。先验证角色感与听感，再考虑收藏、分享。已有文本角色引擎与 TTS 可以分别复用；不必一开始改变通话架构。

下一阶段才是 **睡前陪伴**：短句、较少追问、允许静默，5 / 15 分钟到时收尾。到时必须结束会话和后端生成，不能只静音。雨声、壁炉等环境音独立混音、默认关闭，用户自己调节；它们与人声模型解耦。

## 能确认的参考做法

- [Kindroid 语音](https://kindroid.ai/v2/docs/voice-calls-and-video-calls/)：将语音关联到消息、重播或重新生成、分享音频/带头像短视频。这里只借鉴交互，没有证据证明其使用特定 ASMR 模型。
- [Calm 睡前内容](https://support.calm.com/hc/en-us/articles/360016390734-Using-Calm-s-Content-to-Fall-Asleep)与[定时](https://support.calm.com/hc/en-us/articles/115002582908-How-to-Use-the-Calm-App-to-Fall-Asleep-Sleep-Timers-Playlists)：把内容场景和结束机制作为完整体验。
- [Eleven 表达模式](https://elevenlabs.io/docs/eleven-agents/customization/voice/expressive-mode)与[环境音](https://elevenlabs.io/docs/eleven-agents/customization/voice/audio-environment)：声音表现和循环背景音独立处理。不能把单声道输出包装成双耳录音。
- [Fish Drama 3](https://docs.fish.audio/features/text-to-speech/drama-3)：发声指令只是可测试的控制手段，实际效果需要按音色和语言试听。

以上参考来自官方资料阅读；未声称已实机体验竞品。公开 Demo 的 Eleven 试听已实现，角色晚安/睡前陪伴仍是产品建议。
