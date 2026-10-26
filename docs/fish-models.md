# Fish 原生模型跨语言试听

2026-10-24生成并保存12段日语录音：Ivan Volkov、月見灯两个角色，分别请求`s2-pro`、`s2.1-pro`、`drama-3`，每个模型都有普通和耳语指令两种条件。

2026-10-26补充同样条件的12段英语，页面共24段。切换输出语言不改变角色voice ID，参考录音分别为Ivan英语、月見灯日语；这里不把参考语言冒称为音色创建时的语言设置。

## 控制条件

- 同一角色始终使用同一个现有Fish reference ID，没有重新创建或应用角色音色。
- 所有样本说同一句日语：`今日は、どんな一日だった？無理に話さなくても大丈夫。ゆっくりでいいから、聞かせて。`
- 英语采用对应含义：`How was your day? It's okay if you don't feel like talking. Take your time. I'm here to listen.` 同一语言中的角色、模型、风格使用完全相同正文。
- 普通版不加风格指令；耳语版只在正文前加`[whisper] `。不混入额外emotion、speed或expressivity参数。
- 保存返回MP3原字节，没有MeanVC2、音量归一化、均衡、降噪或其他后期。
- S2 Pro是这两个角色本轮读回的音色配置基线，不代表每个生产会话的实验分流。

这是一组原生TTS试听，既不是原生全双工模型比较，也不是实际通话录音。是否像原角色、日语是否自然、耳语是否可信，仍由人试听判断。

## 模型证据

Fish官方API通过`model`请求header选引擎，正文的`reference_id`选声音。此次经现有试听服务提交精确候选模型；该服务把候选model转发到Fish header，但返回音频时没有回传实际上游模型标识。因此记录的是`requestedModel`，`servedModel`保留`null`，不以音频字节相异冒充模型身份认证。

精确模型名和风格标记来自[Fish模型说明](https://docs.fish.audio/developer-guide/models-pricing/models-overview)、[Drama 3说明](https://docs.fish.audio/features/text-to-speech/drama-3)。官方说明缺失或未知model可能回退，因此不要修改拼写后仍沿用原标签。

公开的[生成记录](../web/public/fish-models/generation-receipts.json)包含请求模型、voice ID、台词、生成整段耗时和文件hash。耗时不是首个音频返回时间，也不是实时通话延迟。Ivan的S2 Pro耳语首次浏览器捕获超时，后来返回但没有保存；做了一次有记录的重试，没有按听感挑选多次结果。

## 如何使用结果

先听每个角色普通说话的三段，判断新引擎是否改善日语同时保留身份；再对比耳语指令。若某一原生TTS方案可接受，它能复用现有角色声音并省去额外变声阶段，但对话仍需要文本生成／轮次调度。不能仅凭TTS音质改善就宣称全双工问题已解决。

这些录音和页面属于实验材料。没有修改角色当前配置、生产实验开关或通话模型。

## 语言变量的边界

本实验检查现有同一声音在不同文本语言下的效果。它没有新建音色，不能据此推断“创建音色时不填语言”优于填写日语或英语。Fish的voice-design语言是提示；TTS的language字段用于文本规范化/对齐，不是改变角色口音的开关。[Voice Design](https://docs.fish.audio/features/voice-design)、[TTS参数](https://docs.fish.audio/api-reference/endpoint/openapi-v1/text-to-speech)
