# Prelulu · Voice Lab

公司独立演示页：[打开 Voice Lab](https://demo-voice-lab.zjudn2013.workers.dev/voice-lab/)。

新增 [Eleven 现场生成与耳语](https://demo-voice-lab.zjudn2013.workers.dev/voice-lab/?tab=eleven)：输入最多600字符的任意台词，在同一个官方参考音色下，独立选择 Eleven v4 / v4 Turbo 和正常 / 耳语。可一次生成正常声与耳语两段，支持重播、下载；不使用预生成录音代替新输入。需要当前 Demo 体验码。

Roger / Sarah 是 Eleven 官方试听音色，不是 Prelulu 角色的克隆。它们用于评估多语言与耳语表现，不能用来证明原角色音色相似度。此入口只合成输入原文，不生成角色回复，不是实时通话。耳语不包含双耳或环境音效果。

固定角色的同一个 Fish voice ID，选择 English、日本語、한국어、繁體中文（台湾用语）或简体中文，比较三种发声模式：

| 页面模式 | 请求模型 | 发声方式 |
| --- | --- | --- |
| 原版 | `s2-pro` | 普通说话，作为配置基线 |
| 语言优化 | `s2.1-pro` | 普通说话，作为多语言候选；不保证每个音色都改善 |
| ASMR · 耳语 | `drama-3` | 同一声音加 `[whisper]` 指令，无双耳或环境音效 |

三个角色为 Ivan、月見灯、Dante Marchetti。Dante于2026-10-26按后台真实互动分选出，替换朝霧朔，使用其官网当前Fish音色。图片与声音参考来自现有公开角色资料，固定voice ID；切换语言不新建声音。页面提供：

- **读原文**：语言按钮填入对应标准句，可直接朗读；45个角色／语言／模型组合均已由已授权的Prelulu试听接口生成。结果明确标为“预生成标准句”，无需体验码。动态Fish服务配置后可合成任意新台词，不自动翻译。
- **同句对比**：按三种模式分别试听同一句标准台词。不同模型和语言始终保留同一个角色voice ID。
- 原声参考、重播、取消、仅在当前页保存的最近试听。支持语音听写的浏览器还可把麦克风转成可编辑文字，但这不是全双工通话。

旧[固定试听页](https://meanvc2-ivan-report.asashiki-qingshuai.workers.dev/?view=fish)继续保留。

## 状态与服务配置

本目录已部署到公司 `demo-voice-lab`。`GET /api/config` 返回动态能力和45个 `sampleKeys`。未配置Fish key时，只有文本完全匹配的标准句可免费读取已有音频；任何新句都会明确禁用／返回503，不用标准句替代用户新输入。`source: sample_cache`不冒称本次生成。

标准句已经可以试听。要合成任意新台词，需要在**这个 Demo Worker**注入：

- `FISH_API_KEY`：启用现场台词合成。
- `DEMO_ACCESS_TOKEN`：分享给体验者的访问码，完整邀请链接可带 `#invite=...`。它不是 Fish 密钥。
- `ELEVENLABS_API_KEY`：启用独立的 Eleven 任意台词合成，不影响 Fish 的配置状态。仅注入专用 Demo Worker，不能进入前端构建或 Git。

生产 Worker 中已有 Fish secret 不会自动被此 Worker 继承。部署权限也不等于能读取生产 secret。由持有测试密钥的人通过 Cloudflare 控制台或本机已配置入口注入，**不把密钥写入 GitHub、浏览器或普通聊天**：

```bash
prelulu-demo secret put FISH_API_KEY
```

按交互提示输入值。若没有本机封装命令，请使用自己的授权 Wrangler 配置执行同名 `secret put`。配置改变后刷新页面；`capabilities=true` 只说明存在配置，实际调用成功仍需一条现场音频验证。

## 构建与发布

```bash
cd live-fish-demo
npm ci
npm run build
node --test worker/*.test.mjs
# 仅在已获授权的公司 Demo 环境：
prelulu-demo deploy --config ./worker/wrangler.jsonc
```

使用 `/voice-lab/` 或根路径打开。Vite资源使用根路径，Worker将`/voice-lab/`映射到构建的index。生产 Prelulu、角色配置和实验分流均不在此模块的写入范围。

## 价格与测量

常规公开TTS价：S2 Pro和S2.1 Pro均$15／百万UTF-8输入字节，Drama3为$50，约3.33倍。页面单独标注截止2026-11-13北京时间01:00的公开活动价；这不是整场通话成本，公司实际账单可能不同。

当前接口完整生成MP3再返回；记录的是服务端完整生成耗时，不是流式首音或通话延迟。API请求中明确选择模型，但不返回供应商独立证明的served model，结果保留`servedModel:null`。

详细后端契约与小范围访问限制见 [worker/README-backend.md](worker/README-backend.md)。

Fish标签评估Fish原生朗读，Eleven标签评估Eleven原生朗读，均不使用Gemini Live或MeanVC2；角色回复入口已移除。公司Worker为原地改名，原ID及已有绑定保持，未更改生产应用。

## Eleven 接口与验证

`POST /api/eleven/synthesize` 接受固定的 `voiceId`（`roger` / `sarah`）、`locale`、`model`、`delivery`（`normal` / `whisper`）及纯文本 `text`。同源校验与现有体验码、并发、请求频率限制共同作用；模型与音色 ID 由服务端白名单决定。耳语只由服务端添加 `[whispers]`，供应商错误正文不会透传。每次点击明确发起合成，无后台轮询生成或自动付费重试。

输出标记 `provider: elevenlabs`、`source: live`（本次生成，不表示 Live 通话）、`requestedModel`、`servedModel: null`，以及完整合成耗时。原始请求 ID 只在供应商返回有效格式时记录；不声称已独立验证供应商执行的模型。

2026-10-26 已通过账户模型列表核对两个模型，并分别实际请求日语音频，均返回 HTTP 200 / MP3。主观声音质量、日语自然度和耳语舒适度由人工试听判断；本机全文件合成耗时不作为通话延迟。
