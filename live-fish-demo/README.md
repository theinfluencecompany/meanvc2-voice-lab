# Prelulu · Fish Voice Lab

公司独立演示页：[打开 Voice Lab](https://demo-qingshuai-lab.zjudn2013.workers.dev/voice-lab/)。

固定角色的同一个 Fish voice ID，选择 English、日本語、한국어、繁體中文（台湾用语）或简体中文，比较三种发声模式：

| 页面模式 | 请求模型 | 发声方式 |
| --- | --- | --- |
| 原版 | `s2-pro` | 普通说话，作为配置基线 |
| 语言优化 | `s2.1-pro` | 普通说话，作为多语言候选；不保证每个音色都改善 |
| ASMR · 耳语 | `drama-3` | 同一声音加 `[whisper]` 指令，无双耳或环境音效 |

三个角色为 Ivan、月見灯、朝霧朔。图片与声音参考来自现有公开角色资料，固定voice ID；切换语言不新建声音。页面提供：

- **读原文**：现场输入台词，直接生成语音；不自动翻译。语言按钮会填入对应的标准台词，方便控制变量。
- **角色回复**：相同的文字模型生成简短回复，再由当前选定的 Fish 模型朗读。角色描述是本演示的简短描述，不冒称生产中的全部记忆和人格。
- **同句对比**：按三种模式分别合成同一句台词。聊天模式会重读最近一条已生成回复，避免不同回答干扰比较。
- 原声参考、重播、取消、仅在当前页保存的最近试听。支持语音听写的浏览器还可把麦克风转成可编辑文字，但这不是全双工通话。

旧[固定试听页](https://meanvc2-ivan-report.asashiki-qingshuai.workers.dev/?view=fish)继续保留。

## 状态与服务配置

本目录代码可部署到已授权的 `demo-qingshuai-lab`。页面和服务是否能生成是两件事：`GET /api/config` 会按当前服务密钥配置返回 `tts/chat` 能力，未配置时页面禁用相应按钮，绝不拿录音冒充本次生成。

需要在**这个 Demo Worker**注入：

- `FISH_API_KEY`：启用现场台词合成。
- `DEMO_ACCESS_TOKEN`：分享给体验者的访问码，完整邀请链接可带 `#invite=...`。它不是 Fish 密钥。
- `GEMINI_API_KEY`：可选，启用“角色回复”；仅有 Fish 时“读原文”和“同句对比”仍可用。
- `GEMINI_MODEL`：可选，使用账号实际可调用的文字模型；默认 `gemini-2.5-flash-lite`。

生产 Worker 中已有 Fish secret 不会自动被此 Worker 继承。部署权限也不等于能读取生产 secret。由持有测试密钥的人通过 Cloudflare 控制台或本机已配置入口注入，**不把密钥写入 GitHub、浏览器或普通聊天**：

```bash
prelulu-demo secret put FISH_API_KEY
prelulu-demo secret put GEMINI_API_KEY
```

按交互提示输入值。若没有本机封装命令，请使用自己的授权 Wrangler 配置执行同名 `secret put`。配置改变后刷新页面；`capabilities=true` 只说明存在配置，实际调用成功仍需一条现场音频验证。

## 构建与发布

```bash
cd live-fish-demo
npm ci
npm run build
node --test worker/worker.test.mjs
# 仅在已获授权的公司 Demo 环境：
prelulu-demo deploy --config ./worker/wrangler.jsonc
```

使用 `/voice-lab/` 或根路径打开。Vite资源使用根路径，Worker将`/voice-lab/`映射到构建的index。生产 Prelulu、角色配置和实验分流均不在此模块的写入范围。

## 价格与测量

常规公开TTS价：S2 Pro和S2.1 Pro均$15／百万UTF-8输入字节，Drama3为$50，约3.33倍。页面单独标注截止2026-11-13北京时间01:00的公开活动价；这不是整场通话成本，公司实际账单可能不同。

当前接口完整生成MP3再返回；记录的是服务端完整生成耗时，不是流式首音或通话延迟。API请求中明确选择模型，但不返回供应商独立证明的served model，结果保留`servedModel:null`。

详细后端契约与小范围访问限制见 [worker/README-backend.md](worker/README-backend.md)。
