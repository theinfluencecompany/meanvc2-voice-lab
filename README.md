# MeanVC2 Voice Lab

Gemini Live → MeanVC2 流式变声的独立实验工具。提供角色原声、官网当前配置生成的试听、历史整段转换，以及实时变声的对照。

**[打开在线体验页面](https://meanvc2-ivan-report.asashiki-qingshuai.workers.dev/)**

**[Fish 新版日语对照](https://meanvc2-ivan-report.asashiki-qingshuai.workers.dev/?view=fish)** — Ivan／月見灯，S2 Pro配置基线、S2.1 Pro、Drama 3，普通与耳语指令共12段原生TTS录音。[生成方式与边界](docs/fish-models.md)

**[同一声源：整段与流式对照](https://meanvc2-ivan-report.asashiki-qingshuai.workers.dev/?view=compare)** — 久世、Ivan，日英四组，直接比较原声、目标参考、官方整段和当前流式。

页面可直接播放固定样音；实时对话需要体验码，且托管者的电脑与语音服务保持在线。体验码通过私下分享获取，不写入公开仓库。

**这是实验快照，不是 Prelulu 生产源码。当前流式转换的音色相似度和音质仍不理想，不应当作已经通过验收的替代方案。**

## 功能

- 五个角色参考：Ivan Volkov、朝霧朔、柚木こはる、月見灯、久世怜司。
- 日语、英语通话和固定样音。
- Gemini 基础声源可选 Schedar、Iapetus、Kore，每种均附日英基准原声。
- Gemini 原声对照、MeanVC2 流式转换、文字试聊、麦克风输入及打断。
- 开发分支新增可选 GPT-Live-1 原声适配，默认 Echo，可选 Shida 日语男声、Harema 日语女声；缺少专用 API 配置时保持不可用。尚未完成真实 API 与听感验收，不经过 MeanVC2。
- 可选 Cloudflare Worker 中转；MeanVC2 始终在运行 Python 服务的电脑 CPU 上执行。

```text
浏览器麦克风或文字 → 本机网关 → Gemini Live（云端生成语音）
                                    ↓
                           MeanVC2（本机 CPU）
                                    ↓
                              浏览器播放
```

角色参考用于确定目标声音；Gemini 基础声源是变声前的声音。切换基础声源影响下一次通话，**不会重新生成页面上的固定转换录音**，这些录音会标出自己的声源。

## 先打开样音页面

需要 Node.js 22.12+ 和 Python 3；不需要 API 密钥或模型。

```bash
git clone https://github.com/theinfluencecompany/meanvc2-voice-lab.git
cd meanvc2-voice-lab
npm --prefix web ci
npm --prefix web run build
python3 start.py --preview-only
```

打开 `http://127.0.0.1:33330/`。仅试听模式不能生成新回复。

## 在自己的电脑运行实时体验

已在 Apple Silicon macOS 上验证本机 CPU 路径。Windows/Linux 尚未实机验证；固定的 PyTorch 运行时不支持 Intel Mac。

需要 Python 3.11、Git，以及有可用 Gemini Live 模型权限的 API Key。网页端不用填写 API Key，也不需要本地体验码。

```bash
python3.11 setup.py
python3.11 configure.py
python3.11 start.py
```

`setup.py` 创建隔离环境、安装依赖、获取固定版本的 MeanVC2 源码，并下载约 1.65 GB 模型。`configure.py` 通过隐藏输入保存 API Key；模型名称需填写你自己的 API 账号实际可用的 Live 模型。密钥保存在被 Git 忽略的本地配置中。

`start.py` 启动 `33330`（网页）、`8773`（Gemini 网关）、`8771`（CPU 转换服务），已有端口被占用时不会杀掉其他进程。保持终端打开，Ctrl+C 停止本次启动的服务。电脑休眠、退出服务或断网后，实时通话不可用；固定样音仍可单独播放。

页面操作：选角色和语言 → 选基础声音并试听 → 连接体验服务 → 开始通话或先用文字试聊。通话结束后可重新选择基础声音。

### GPT-Live-1 / Echo 开发入口

见 [官方协议与实现边界](docs/gpt-live1-protocol.md) 及 [连续变声的后续验收](docs/continuous-voice-conversion.md)。这是独立原声模式，需要有 GPT-Live-1 权限的 API Key；现有 Gemini Key、ChatGPT 订阅不能替代该配置。配置方法以 `configure_openai.py` 的提示为准，凭据只写入本地 `.env.local`。

该模式持续发送麦克风（包括静音），并行接收 Echo 音频；不以本地音量检测切断会话，不借用 Gemini 的句末事件。当前仅支持语音输入，不把文字消息伪装成用户语音。缺少凭据时页面只显示不可用入口，不能据此声称已经体验过全双工。

取得权限后，可显式运行一次约20秒的计费API检查：

```bash
python3.11 configure_openai.py
.venv/bin/python probe_openai_live.py --input-wav web/public/source-voices/Schedar-ja.wav --seconds 20
```

该脚本按真实速度发送录音及其后的静音，保存Echo原声和会话记录到被Git忽略的`runtime/openai-live-probe/`。即使收到音频，也还需要真人重叠说话验收，不能将录音回放结果冒充真实对话效果。

可增加 `--voice shida` 或 `--voice harema` 比较官方日语声音。之后还可评估 [原生文字定制声音](docs/native-voice-options.md)，省去外部变声；它不保证复刻现有 Fish 声线。

## 重做同源对照

页面顶部“整段 / 流式同源对照”使用同一份16kHz输入、角色参考、模型权重及初始随机状态。两路仍分别采用官方和当前实时的实现，适合比较实际结果，不能把差异归因于单个参数。

模型安装完成后可重新生成，再构建页面：

```bash
.venv/bin/python scripts/compare_same_source.py
python3 scripts/publish_same_source.py
npm --prefix web run build
```

该过程不调用语音 API，使用已保存的 Gemini Schedar 录音；处理时间仅是本机离线计算，不是通话延迟。

## 可选：分享公网体验

见 [Cloudflare 部署说明](cloudflare/README.md)。Cloudflare 只托管静态页面和转发音频，**不会替你提供 MeanVC2 算力**。这个仓库没有包含现有体验站的访问凭证，也不会自动连到原作者的电脑。

## 验证

```bash
npm --prefix web run check
npm --prefix web run build
node --test cloudflare/worker.test.mjs
node --test web/test_playback.mjs
.venv/bin/python -m unittest discover -s work/ivan-jp-pilot/local-call -p test_quality_gateway.py
.venv/bin/python -m unittest discover -s work/ivan-jp-pilot/live-source -p test_gemini_bridge.py
.venv/bin/python -m unittest discover -s work/ivan-jp-pilot/live-source -p test_openai_live.py
.venv/bin/python -m unittest discover -s cloudflare -p test_local_bridge.py
.venv/bin/python work/ivan-jp-pilot/voice-service/check_portable.py
```

Windows 把 `.venv/bin/python` 换成 `.venv\Scripts\python.exe`。前几项验证路由、格式、生命周期与页面构建；最后一项使用本地模型处理固定音频，不调用 Gemini。测试通过不代表音质通过人工验收。

## 已知限制和样音来源

- 当前为语音实验，没有视频嘴型、LiveKit 或 Prelulu 生产接入。GPT-Live-1 / Echo 处于独立协议适配阶段，尚未完成真实全双工对话验收。
- 一次支持一个公网通话。本机延迟不等于生产端到端延迟。
- 流式适配采用已到达的语音特征和分块声码器，不能保证与整段转换相同。源码中的 `prefix_quality` 是实验模式名称，不是音质保证。
- 久世的早期整段英文样本采用 Breeze Neo 源音；当前流式录音采用 Gemini Live，台词也不同。它们不是严格的同源控制变量实验。
- “官网当前音色”样音按 2026-10-23 角色的 Fish 配置生成，是 TTS 试听，不是官网通话录音，也不保证持续反映官网后续配置。
- 本仓库包含角色参考、生成样音和轻量缓存，不包含用户通话录音、登录状态或 API 密钥。

## 上游与许可

[MeanVC2](https://github.com/ASLP-lab/MeanVC2) 固定版本：`13acf84c1bf135ea5edad9c245b345289b06b33e`。安装器从上游获取源码和权重，不把完整上游仓库打包进本项目。上游 README 和模型卡声明 Apache-2.0；模型、依赖和样音分别遵循其原有条款。浏览器依赖声明见 [THIRD-PARTY-NOTICES](web/THIRD-PARTY-NOTICES.txt)。

说话人提取使用的 [WavLM finetune 镜像](https://huggingface.co/bezzam/wavlm_large_finetune_seed_tts_eval) 模型卡标注 **CC-BY-SA-3.0**，它不是 MeanVC2 的 Apache-2.0 权重。下载源与 SHA256 详见 `vendor/models.json`；`vendor/wavlm_large_cfg.pt` 是对应的轻量模型配置，不是模型权重。人物参考和生成样音不因本仓库公开而被重新授权。
