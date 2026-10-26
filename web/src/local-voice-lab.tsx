import { useEffect, useRef, useState } from "react";
import { z } from "zod";
import { Button, Input } from "./ui";
import { loadAudioComparisons, type AudioComparison } from "./comparison-data";
import { LocalVoicePlayback } from "./playback";
import microphoneWorkletUrl from "./microphone-worklet.js?url";
import ivanOriginalEnglishUrl from "./ivan-original-en.mp3?url";
import sakuReferenceUrl from "./saku-reference-ja.mp3?url";
import koharuReferenceUrl from "./koharu-reference-ja.mp3?url";
import loveReferenceUrl from "./love-reference-ja.mp3?url";
import reijiReferenceUrl from "./reiji-reference-ja.mp3?url";
import reijiEarlyWholeUrl from "./reiji-early-whole-en.wav?url";
import comparisonExperimentUrl from "./current-converted.wav?url";

const qualityComparisons = [{ label: "当前转换试听 · 日语", url: comparisonExperimentUrl }];
const PUBLIC_ORIGIN = import.meta.env.VITE_PUBLIC_ORIGIN ?? "";
const publicModes = new Set(["gemini_vc_quality", "gemini_native", "gpt_live_native"]);
const modeLabels: Record<string, string> = { gemini_vc_quality: "角色对话 · 转换声音", gemini_native: "Gemini 原声 · 对比体验", gpt_live_native: "GPT-Live-1 · 原声" };
const isLocalPage = () => ["localhost", "127.0.0.1", "[::1]"].includes(window.location.hostname);
const isAllowedPage = () => window.location.origin === PUBLIC_ORIGIN || isLocalPage();

const LanguageSchema = z.enum(["ja", "en"]);
type Language = z.infer<typeof LanguageSchema>;
const languageLabels: Record<Language, string> = { ja: "日语", en: "英语" };
const SourceVoiceSchema = z.enum(["Schedar", "Iapetus", "Kore"]);
const GptVoiceSchema = z.enum(["echo", "shida", "harema"]);
type GptVoice = z.infer<typeof GptVoiceSchema>;
const gptVoiceLabels: Record<GptVoice, string> = { echo: "Echo · 默认", shida: "Shida · 日语男声", harema: "Harema · 日语女声" };
type SourceVoice = z.infer<typeof SourceVoiceSchema>;
const sourceVoices: { id: SourceVoice; label: string }[] = [
  { id: "Schedar", label: "Schedar · 平稳男声" },
  { id: "Iapetus", label: "Iapetus · 清晰男声" },
  { id: "Kore", label: "Kore · 坚定女声" },
];
const defaultSourceVoice = (id: string): SourceVoice => ["koharu", "love"].includes(id) ? "Kore" : "Schedar";
const CharacterIdSchema = z.enum(["ivan", "saku", "koharu", "love", "reiji"]);
type CharacterId = z.infer<typeof CharacterIdSchema>;
const characters: { id: CharacterId; name: string; referenceLanguage: string; referenceUrl: string }[] = [
  { id: "ivan", name: "Ivan Volkov", referenceLanguage: "英语", referenceUrl: ivanOriginalEnglishUrl },
  { id: "saku", name: "朝霧 朔", referenceLanguage: "日语", referenceUrl: sakuReferenceUrl },
  { id: "koharu", name: "柚木 こはる", referenceLanguage: "日语", referenceUrl: koharuReferenceUrl },
  { id: "love", name: "月見 灯", referenceLanguage: "日语", referenceUrl: loveReferenceUrl },
  { id: "reiji", name: "久世 怜司", referenceLanguage: "日语", referenceUrl: reijiReferenceUrl },
];
const CharacterCapabilitySchema = z.object({ id: CharacterIdSchema, name: z.string(), reference_language: z.string(), gemini_voice: z.string(), available: z.boolean().optional().default(true) });
type CharacterCapability = z.infer<typeof CharacterCapabilitySchema>;
const ModeSchema = z.enum(["mic_vc", "gemini_vc", "gemini_native", "gemini_vc_quality", "gpt_live_native"]);
const CapabilitySchema = z.object({ id: ModeSchema, label: z.string(), available: z.boolean(), reason: z.string().optional() });
const ServerMessage = z.discriminatedUnion("type", [
  z.object({ type: z.literal("capabilities"), modes: z.array(CapabilitySchema), character: z.string(), characters: z.array(CharacterCapabilitySchema).optional(), languages: z.array(LanguageSchema).optional(), source_voices: z.array(SourceVoiceSchema).optional(), gpt_live_voices: z.array(GptVoiceSchema).optional() }),
  z.object({ type: z.literal("ready"), mode: ModeSchema, model: z.string(), sample_rate: z.number(), character_id: CharacterIdSchema.optional(), language: LanguageSchema.optional(), source_voice: z.enum(["Schedar", "Iapetus", "Kore", "echo", "shida", "harema"]).optional() }),
  z.object({ type: z.literal("audio"), turn_id: z.string(), pcm: z.string().max(384000), sample_rate: z.number(), sequence: z.number().int().nonnegative() }),
  z.object({ type: z.literal("cancel"), turn_id: z.string() }),
  z.object({ type: z.literal("end"), turn_id: z.string() }),
  z.object({ type: z.literal("transcript"), role: z.string(), text: z.string() }),
  z.object({ type: z.literal("metrics"), first_output_ms: z.number(), label: z.string() }),
  z.object({ type: z.literal("error"), message: z.string() }),
]);
const MicrophonePacket = z.object({ pcm: z.instanceof(ArrayBuffer), rms: z.number() });
type Mode = z.infer<typeof ModeSchema>;
type Capability = z.infer<typeof CapabilitySchema>;
type Phase = "offline" | "checking" | "idle" | "starting" | "live";
type AudioResources = { context: AudioContext; stream: MediaStream | null; source: MediaStreamAudioSourceNode | null; node: AudioWorkletNode | null; silent: GainNode | null; playback: LocalVoicePlayback };

/** Shared preview of the existing local voice UI. No production Prelulu session is created. */
export function LocalVoiceLab() {
  const [local, setLocal] = useState(false);
  const [officialComparisons, setOfficialComparisons] = useState<AudioComparison[]>([]);
  const [invite, setInvite] = useState(() => new URLSearchParams(window.location.hash.slice(1)).get("invite") ?? "");
  const connectionAttempt = useRef(0);
  const [phase, setPhase] = useState<Phase>("offline");
  const [modes, setModes] = useState<Capability[]>([{ id: "gemini_vc_quality", label: "日语对话 · 转换声音", available: true }, { id: "gemini_native", label: "Gemini 原声 · 对比体验", available: true }, { id: "gpt_live_native", label: "GPT-Live-1 · Echo 原声", available: false, reason: "连接服务后检查 GPT-Live API 配置；此链路尚未完成真实通话验收。" }]);
  const [language, setLanguage] = useState<Language>("ja");
  const [availableLanguages, setAvailableLanguages] = useState<Language[]>([]);
  const runningLanguage = useRef<Language>("ja");
  const [characterId, setCharacterId] = useState<CharacterId>("ivan");
  const [characterCapabilities, setCharacterCapabilities] = useState<CharacterCapability[]>([]);
  const runningCharacter = useRef<CharacterId>("ivan");
  const [sourceVoice, setSourceVoice] = useState<SourceVoice>("Schedar");
  const [availableSourceVoices, setAvailableSourceVoices] = useState<SourceVoice[]>([]);
  const [gptVoice, setGptVoice] = useState<GptVoice>("echo");
  const [availableGptVoices, setAvailableGptVoices] = useState<GptVoice[]>([]);
  const runningSourceVoice = useRef<SourceVoice | GptVoice>("Schedar");
  const [mode, setMode] = useState<Mode>("gemini_vc_quality");
  const [status, setStatus] = useState("点击连接体验服务，然后开始通话或文字试聊。");
  const [error, setError] = useState("");
  const [model, setModel] = useState("");
  const [metric, setMetric] = useState<string | null>(null);
  const [playbackMs, setPlaybackMs] = useState<number | null>(null);
  const [underruns, setUnderruns] = useState({ count: 0, milliseconds: 0 });
  const [transcript, setTranscript] = useState<{ role: string; text: string }[]>([]);
  const [text, setText] = useState("");
  const [level, setLevel] = useState(0);
  const textInputOnly = useRef(false);
  const socket = useRef<WebSocket | null>(null);
  const audio = useRef<AudioResources | null>(null);
  const referenceAudio = useRef<HTMLAudioElement | null>(null);
  const sourceBaselineAudio = useRef<HTMLAudioElement | null>(null);
  const [baselineError, setBaselineError] = useState(false);
  const reijiEarlyAudio = useRef<HTMLAudioElement | null>(null);
  const comparisonAudio = useRef<(HTMLAudioElement | null)[]>([]);
  const officialComparisonAudio = useRef<(HTMLAudioElement | null)[]>([]);
  const ready = useRef(false);
  const alive = useRef(false);
  const revision = useRef(0);
  const runningMode = useRef<Mode>("mic_vc");
  const speech = useRef({ loud: 0, quiet: 0, armed: true });
  const startupTimeout = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  function disposeAudio() {
    if (startupTimeout.current !== undefined) clearTimeout(startupTimeout.current);
    startupTimeout.current = undefined;
    revision.current++;
    ready.current = false;
    const current = audio.current;
    audio.current = null;
    if (!current) return;
    if (current.node) current.node.port.onmessage = null;
    current.playback.dispose();
    current.stream?.getTracks().forEach((track) => track.stop());
    current.source?.disconnect(); current.node?.disconnect(); current.silent?.disconnect();
    if (current.context.state !== "closed") void current.context.close().catch(() => {});
  }

  function send(message: object) {
    if (socket.current?.readyState === WebSocket.OPEN) socket.current.send(JSON.stringify(message));
  }

  function interrupt() {
    if (runningMode.current === "gpt_live_native") return;
    audio.current?.playback.cancel();
    send({ type: "interrupt" });
    setStatus(runningMode.current === "mic_vc" ? "已清空旧声音；继续说话可重新试听。" : "已打断，继续说话。 ");
  }

  function fail(message: string) {
    disposeAudio();
    const current = socket.current; socket.current = null; current?.close();
    if (alive.current) { setError(message); setPhase("offline"); setStatus("已停止"); setLevel(0); }
  }

  async function connect() {
    if (!isAllowedPage()) return;
    const attempt = ++connectionAttempt.current;
    const previous = socket.current; socket.current = null; previous?.close();
    disposeAudio();
    setModes([]); setCharacterCapabilities([]); setAvailableLanguages([]); setAvailableSourceVoices([]); setError(""); setPhase("checking"); setStatus("连接语音服务…");
    let gateway: URL;
    try {
      const response = await fetch("./share-config.json", { cache: "no-store" });
      if (!response.ok) throw new Error("体验服务暂未启动，请稍后重试。");
      const config = z.object({ gatewayUrl: z.string().url() }).parse(await response.json());
      gateway = new URL(config.gatewayUrl);
      if (gateway.protocol !== "wss:" && !(gateway.protocol === "ws:" && ["127.0.0.1", "localhost"].includes(gateway.hostname))) throw new Error("体验服务地址无效。");
      if (!isLocalPage()) {
        if (!invite.trim()) { setPhase("offline"); setStatus("填写体验码后连接，或使用完整的邀请链接。"); return; }
        gateway.searchParams.set("token", invite.trim());
      }
    } catch (cause) {
      if (alive.current && connectionAttempt.current === attempt) fail(cause instanceof Error ? cause.message : "体验服务暂未就绪。");
      return;
    }
    if (!alive.current || connectionAttempt.current !== attempt) return;
    const ws = new WebSocket(gateway);
    socket.current = ws;
    ws.onmessage = (event: MessageEvent<unknown>) => {
      if (socket.current !== ws || !alive.current) return;
      try {
        if (typeof event.data !== "string" || event.data.length > 400000) throw new Error("服务消息格式异常。");
        const message = ServerMessage.parse(JSON.parse(event.data));
        if (message.type === "capabilities") {
          setAvailableLanguages(message.languages ?? ["ja"]);
          setAvailableSourceVoices(message.source_voices ?? []);
          setAvailableGptVoices(message.gpt_live_voices ?? ["echo"]);
          setCharacterCapabilities(message.characters ?? [{ id: "ivan", name: "Ivan Volkov", reference_language: "en", gemini_voice: "Schedar", available: true }]);
          setModes(message.modes.filter((entry) => publicModes.has(entry.id))); setPhase("idle"); setStatus("服务已就绪，可以开始体验。");
          setMode((current) => message.modes.some((entry) => entry.id === current && entry.available) ? current : message.modes.find((entry) => entry.available && publicModes.has(entry.id))?.id ?? current);
        } else if (message.type === "ready") {
          if (message.language && message.language !== runningLanguage.current) throw new Error("服务返回的语言不匹配，已停止。");
          if ((message.character_id ?? "ivan") !== runningCharacter.current) throw new Error("服务返回的角色不匹配，已停止。");
          if (message.mode !== "mic_vc" && message.source_voice !== runningSourceVoice.current) throw new Error("服务返回的基础声源不匹配，已停止，请刷新后重新连接。");
          if (startupTimeout.current !== undefined) clearTimeout(startupTimeout.current);
          startupTimeout.current = undefined;
          ready.current = true; setPhase("live"); setModel(message.model);
          setStatus(message.mode === "mic_vc" ? "现在说话，会实时听到转换后的角色音色。" : textInputOnly.current ? "已接通，输入文字即可听到实时语音回答。麦克风未开启。" : `已接通，可以用${languageLabels[runningLanguage.current]}交谈。`);
        } else if (message.type === "audio") {
          audio.current?.playback.append(message.turn_id, message.pcm, message.sample_rate, message.sequence);
          setStatus(runningMode.current === "mic_vc" ? "正在实时播放你的变声音频。" : "正在播放实时语音回复。");
        } else if (message.type === "cancel") {
          audio.current?.playback.cancel(message.turn_id);
        } else if (message.type === "transcript") {
          setTranscript((old) => {
            if (runningMode.current !== "gpt_live_native") return [...old.slice(-39), { role: message.role, text: message.text }];
            const previous = old.at(-1);
            // Live events are deltas, not completed turns. Append exact text
            // without inserting spaces; keep at most 12k displayed characters.
            const updated = previous?.role === message.role
              ? [...old.slice(0, -1), { ...previous, text: (previous.text + message.text).slice(-12000) }]
              : [...old, { role: message.role, text: message.text.slice(-12000) }];
            let remaining = 12000;
            const bounded: { role: string; text: string }[] = [];
            for (let index = updated.length - 1; index >= 0 && remaining > 0; index--) {
              const line = updated[index];
              const text = line.text.slice(-remaining);
              bounded.unshift({ role: line.role, text });
              remaining -= text.length;
            }
            return bounded;
          });
        } else if (message.type === "metrics") {
          setMetric(`${message.label}：${Math.round(message.first_output_ms)} ms`);
        } else if (message.type === "error") fail(message.message);
      } catch (cause) {
        fail(cause instanceof Error && ! (cause instanceof z.ZodError) ? cause.message : "服务协议不匹配，已停止。");
      }
    };
    ws.onerror = () => { if (socket.current === ws) fail("暂时无法连接体验服务，请检查体验码，或稍后重试。"); };
    ws.onclose = () => { if (socket.current === ws) fail("体验服务连接已断开，请重新连接。"); };
  }

  useEffect(() => {
    alive.current = true;
    const allowed = isAllowedPage();
    setLocal(allowed);
    const comparisonsAbort = new AbortController();
    if (allowed) void loadAudioComparisons(comparisonsAbort.signal).then((items) => {
      if (alive.current) setOfficialComparisons(items);
    }).catch(() => {});

    return () => {
      comparisonsAbort.abort();
      alive.current = false;
      connectionAttempt.current++;
      send({ type: "stop" });
      const current = socket.current; socket.current = null; current?.close();
      disposeAudio();
    };
  }, []);

  async function start(withMicrophone = true) {
    if (phase !== "idle" || !modes.some((entry) => entry.id === mode && entry.available) || (mode !== "gpt_live_native" && !characterCapabilities.some((entry) => entry.id === characterId && entry.available)) || !availableLanguages.includes(language) || (mode === "gpt_live_native" ? !availableGptVoices.includes(gptVoice) : !availableSourceVoices.includes(sourceVoice))) return;
    if (!withMicrophone && (mode === "mic_vc" || mode === "gpt_live_native")) return;
    referenceAudio.current?.pause();
    sourceBaselineAudio.current?.pause();
    reijiEarlyAudio.current?.pause();
    comparisonAudio.current.forEach((player) => player?.pause());
    officialComparisonAudio.current.forEach((player) => player?.pause());
    const attempt = ++revision.current;
    textInputOnly.current = !withMicrophone;
    runningCharacter.current = characterId;
    runningLanguage.current = language;
    runningSourceVoice.current = mode === "gpt_live_native" ? gptVoice : sourceVoice;
    runningMode.current = mode; speech.current = { loud: 0, quiet: 0, armed: true };
    setPhase("starting"); setStatus(withMicrophone ? "等待麦克风授权…" : "正在连接实时语音…"); setError(""); setMetric(null); setPlaybackMs(null); setTranscript([]);
    setUnderruns({ count: 0, milliseconds: 0 });
    let context: AudioContext | null = null;
    let stream: MediaStream | null = null;
    try {
      context = new AudioContext({ sampleRate: 16000, latencyHint: "interactive" });
      await context.resume();
      if (context.sampleRate !== 16000) throw new Error("浏览器没有提供 16 kHz 音频环境，请使用 Chrome 或 Ego Lite。");
      if (withMicrophone) {
        stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
        await context.audioWorklet.addModule(microphoneWorkletUrl);
      }
      if (revision.current !== attempt || !alive.current) { stream?.getTracks().forEach((track) => track.stop()); await context.close(); return; }
      const node = stream ? new AudioWorkletNode(context, "local-voice-microphone") : null;
      const source = stream ? context.createMediaStreamSource(stream) : null;
      const silent = stream ? context.createGain() : null;
      if (silent) silent.gain.value = 0;
      const playback = new LocalVoicePlayback(context, (milliseconds) => { if (alive.current) setPlaybackMs(Math.round(milliseconds)); }, (milliseconds) => {
        if (alive.current) setUnderruns((previous) => ({ count: previous.count + 1, milliseconds: previous.milliseconds + milliseconds }));
      }, fail);
      audio.current = { context, stream, node, source, silent, playback };
      if (node) node.port.onmessage = (event: MessageEvent<unknown>) => {
        const parsed = MicrophonePacket.safeParse(event.data);
        if (!parsed.success || !ready.current) return;
        const { pcm, rms } = parsed.data;
        if (pcm.byteLength !== 640) { fail("麦克风音频包长度异常。"); return; }
        setLevel(Math.min(100, Math.round(rms * 400)));
        const ws = socket.current;
        if (ws?.readyState !== WebSocket.OPEN) return;
        if (ws.bufferedAmount > 64000) { fail("麦克风发送积压超过 2 秒，已停止。"); return; }
        ws.send(pcm);
        if (runningMode.current !== "mic_vc" && runningMode.current !== "gpt_live_native") {
          const vad = speech.current;
          if (rms > 0.035) { vad.loud++; vad.quiet = 0; } else { vad.loud = 0; vad.quiet++; }
          if (vad.quiet >= 15) vad.armed = true;
          if (vad.armed && vad.loud >= 5 && playback.playing) { vad.armed = false; interrupt(); }
        }
      };
      if (source && node && silent) { source.connect(node); node.connect(silent); silent.connect(context.destination); }
      send({ type: "start", mode, character_id: characterId, language, source_voice: runningSourceVoice.current });
      startupTimeout.current = setTimeout(() => {
        if (revision.current === attempt && !ready.current) fail("语音服务连接超过 30 秒，已停止。可重新连接后再试。");
      }, 30000);
      setStatus("正在连接音频链路…");
    } catch (cause) {
      stream?.getTracks().forEach((track) => track.stop());
      if (context && context.state !== "closed") await context.close();
      if (revision.current === attempt && alive.current) fail(cause instanceof Error ? cause.message : "麦克风启动失败。");
    }
  }

  function resetSelectionContent() {
    referenceAudio.current?.pause();
    sourceBaselineAudio.current?.pause();
    reijiEarlyAudio.current?.pause();
    comparisonAudio.current.forEach((player) => player?.pause());
    officialComparisonAudio.current.forEach((player) => player?.pause());
    audio.current?.playback.cancel();
    setTranscript([]); setText(""); setMetric(null); setPlaybackMs(null); setModel(""); setError("");
    setUnderruns({ count: 0, milliseconds: 0 });
    setBaselineError(false);
  }

  function chooseCharacter(id: CharacterId) {
    if (phase === "starting" || phase === "live") return;
    resetSelectionContent();
    setCharacterId(id);
    setSourceVoice(defaultSourceVoice(id));
  }

  function chooseLanguage(value: Language) {
    if (phase === "starting" || phase === "live") return;
    resetSelectionContent();
    setLanguage(value);
  }

  function chooseSourceVoice(value: SourceVoice) {
    if (phase === "starting" || phase === "live") return;
    resetSelectionContent();
    setSourceVoice(value);
  }

  function stop() {
    connectionAttempt.current++;
    send({ type: "stop" });
    const current = socket.current; socket.current = null; current?.close();
    disposeAudio(); setPhase("offline"); setStatus("已结束，麦克风已关闭。"); setLevel(0);
  }

  if (!local) return <main className="p-6 text-muted-foreground">请使用完整的体验链接打开。</main>;
  const officialComparison = officialComparisons.find((entry) => entry.characterId === characterId && entry.language === language);
  const character = characters.find((entry) => entry.id === characterId)!;
  const characterCapability = characterCapabilities.find((entry) => entry.id === characterId);
  const characterAvailable = mode === "gpt_live_native" || characterCapability?.available === true;
  const geminiVoiceLabel = sourceVoices.find((entry) => entry.id === sourceVoice)!.label;
  const sourceVoiceAvailable = mode === "gpt_live_native" ? availableGptVoices.includes(gptVoice) : availableSourceVoices.includes(sourceVoice);
  const languageAvailable = availableLanguages.includes(language);
  const languageLabel = languageLabels[language];
  const selected = modes.find((entry) => entry.id === mode);
  const busy = phase === "starting" || phase === "live";
  return <main className="mx-auto flex min-h-screen max-w-3xl flex-col gap-6 px-4 py-8 text-foreground" data-testid="local-voice-lab">
    <header className="flex items-center justify-between gap-4">
      <a href="/" className="font-display text-xl">Prelulu</a>
      <nav className="flex flex-wrap justify-end gap-3 text-sm text-muted-foreground" aria-label="试听实验">
        <a href={`?view=fish${window.location.hash}`}>Fish 新版对照</a>
        <a href={`?view=compare${window.location.hash}`}>整段 / 流式同源对照</a>
      </nav>
    </header>
    <section className="rounded-2xl border border-border bg-card p-6 shadow-e1">
      <label htmlFor="voice-character" className="text-sm text-muted-foreground">角色</label>
      <select id="voice-character" aria-label="选择角色" className="ui-input mt-2" value={characterId} disabled={busy || phase === "checking"} onChange={(event) => chooseCharacter(CharacterIdSchema.parse(event.target.value))}>
        {characters.map((entry) => <option key={entry.id} value={entry.id}>{entry.name}</option>)}
      </select>
      <label htmlFor="voice-language" className="mt-4 block text-sm text-muted-foreground">对话语言</label>
      <select id="voice-language" aria-label="对话语言" className="ui-input mt-2" value={language} disabled={busy || phase === "checking"} onChange={(event) => chooseLanguage(LanguageSchema.parse(event.target.value))}>
        <option value="ja">日语</option>
        <option value="en">英语</option>
      </select>
      <p className="eyebrow mt-4 text-muted-foreground">{character.name}</p>
      <h1 className="mt-2 font-display text-2xl">语音体验</h1>
      <p className="mt-3 text-sm text-muted-foreground">直接和角色说话，或输入文字听回复。建议戴耳机；一次仅支持一人通话。</p>
      {mode !== "gpt_live_native" && <section className="mt-4 rounded-xl border border-border bg-background p-4" data-testid="source-voice-settings">
        <h2 className="text-sm font-medium">Gemini 基础声源</h2>
        <p className="mt-1 text-xs text-muted-foreground">选择变声前的声音，用于下一次通话；角色的目标音色参考不变。</p>
        <label htmlFor="source-voice" className="mt-3 block text-sm text-muted-foreground">基础声音</label>
        <select id="source-voice" aria-label="Gemini 基础声音" className="ui-input mt-2" value={sourceVoice} disabled={busy || phase === "checking"} onChange={(event) => chooseSourceVoice(SourceVoiceSchema.parse(event.target.value))}>
          {sourceVoices.map((entry) => <option key={entry.id} value={entry.id}>{entry.label}</option>)}
        </select>
        <p className="mt-3 text-sm">{geminiVoiceLabel} · {languageLabel}基准原声</p>
        <audio key={`${sourceVoice}-${language}`} ref={sourceBaselineAudio} src={`./source-voices/${sourceVoice}-${language}.wav`} controls={!busy} preload="metadata" className="mt-2 w-full" aria-label="Gemini 基准原声试听" data-testid="source-voice-baseline" onError={() => setBaselineError(true)} onPlay={(event) => {
          if (busy) event.currentTarget.pause();
          referenceAudio.current?.pause(); reijiEarlyAudio.current?.pause();
          comparisonAudio.current.forEach((player) => player?.pause());
          officialComparisonAudio.current.forEach((player) => player?.pause());
        }} />
        <p className="mt-2 text-xs text-muted-foreground">固定台词试听，未经 MeanVC2。实际对话的语气和节奏会随内容变化。</p>
        {baselineError && <p className="mt-2 text-xs text-destructive">基准音频加载失败，请刷新重试。</p>}
        {busy && <p className="mt-2 text-xs text-muted-foreground">结束本次通话后可更换基础声音。</p>}
      </section>}
      {mode === "gpt_live_native" && <section className="mt-4 rounded-xl border border-border bg-background p-4" data-testid="gpt-live-source">
        <h2 className="text-sm font-medium">GPT-Live-1 · {gptVoiceLabels[gptVoice]}</h2>
        <label className="mt-3 block text-sm" htmlFor="gpt-voice">原生声音</label>
        <select id="gpt-voice" aria-label="GPT Live 原生声音" className="ui-input mt-2" value={gptVoice} disabled={busy || phase === "checking"} onChange={(event) => { resetSelectionContent(); setGptVoice(GptVoiceSchema.parse(event.target.value)); }}>
          {Object.entries(gptVoiceLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <p className="mt-2 text-sm">原生连续语音，收听与输出同时进行；本模式不经过 MeanVC2。配置 GPT Live 权限后，可开始通话试听这三种声音。</p>
        <p className="mt-2 text-xs text-muted-foreground">需要麦克风。听说切换由模型处理，不按本机音量自动打断；结束后可切换模式。</p>
      </section>}
      <section className="mt-4 rounded-xl border border-border bg-background p-4" aria-labelledby="character-original-voice-heading">
        <h2 id="character-original-voice-heading" className="text-sm font-medium">{character.name} 原声 · {character.referenceLanguage}</h2>
        <p className="mt-1 text-xs text-muted-foreground">这是本次转换使用的角色音色参考，可以对比声音是否相像。</p>
        <audio key={character.id} ref={referenceAudio} src={character.referenceUrl} controls={!busy} preload="metadata" className="mt-3 w-full" aria-label={`播放 ${character.name} 原本的${character.referenceLanguage}音频`} data-testid="character-reference-audio" onPlay={(event) => { if (busy) event.currentTarget.pause(); sourceBaselineAudio.current?.pause(); reijiEarlyAudio.current?.pause(); comparisonAudio.current.forEach((player) => player?.pause()); officialComparisonAudio.current.forEach((player) => player?.pause()); }} />
        {busy && <p className="mt-2 text-xs text-muted-foreground">通话中暂停原声试听；结束通话后可再次播放对比。</p>}
      </section>
      {characterId === "reiji" && <section className="mt-4 rounded-xl border border-border bg-background p-4" data-testid="reiji-early-whole-conversion">
        <h2 className="text-sm font-medium">早期 MeanVC2 整段转换 · 英语</h2>
        <p className="mt-1 text-xs text-muted-foreground">10月20日保存的整段转换。声源为 Breeze Neo；当前流式使用 Gemini Live，台词也不同。</p>
        <audio ref={reijiEarlyAudio} src={reijiEarlyWholeUrl} controls={!busy} preload="metadata" className="mt-3 w-full" aria-label="久世怜司早期 MeanVC2 整段转换英语录音" onPlay={(event) => {
          if (busy) event.currentTarget.pause();
          sourceBaselineAudio.current?.pause();
          referenceAudio.current?.pause();
          comparisonAudio.current.forEach((player) => player?.pause());
          officialComparisonAudio.current.forEach((player) => player?.pause());
        }} />
      </section>}
      {officialComparison && <section className="mt-4 rounded-xl border border-border bg-background p-4" data-testid="official-voice-comparison">
        <h2 className="text-sm font-medium">官网当前声音 / MeanVC2 流式转换</h2>
        <p className="mt-1 text-xs text-muted-foreground">固定转换录音的基础声源：{officialComparison.sourceVoice ?? "见来源说明"}。更改上方声音不会重新生成这些录音，请开始通话听新设置。</p>
        <p className="mt-1 text-xs text-muted-foreground">{officialComparison.sameText ? "使用同一句内容，直接对比发音与音色。" : "两段音频可直接试听对比。"}</p>
        <div className="mt-3 grid gap-4 sm:grid-cols-2">
          {officialComparison.audio.map((item, index) => <div key={item.audioRelativeUrl}>
            <p className="text-sm font-medium">{item.label}</p>
            <audio ref={(player) => { officialComparisonAudio.current[index] = player; }} src={item.audioRelativeUrl} controls={!busy} preload="metadata" className="mt-2 w-full" aria-label={item.label} onError={() => setOfficialComparisons((old) => old.filter((entry) => entry !== officialComparison))} onPlay={(event) => {
              if (busy) event.currentTarget.pause();
              sourceBaselineAudio.current?.pause();
              referenceAudio.current?.pause();
              reijiEarlyAudio.current?.pause();
              comparisonAudio.current.forEach((player) => player?.pause());
              officialComparisonAudio.current.forEach((player) => { if (player !== event.currentTarget) player?.pause(); });
            }} />
            {item.transcript && <p className="mt-2 text-xs text-muted-foreground">{item.transcript}</p>}
          </div>)}
        </div>
        {officialComparison.transcript && <p className="mt-3 text-sm">{officialComparison.transcript}</p>}
        <p className="mt-2 text-xs text-muted-foreground">{officialComparison.provenanceNote}</p>
      </section>}
      {!officialComparison && characterId === "ivan" && language === "ja" && <details className="mt-4 rounded-xl border border-border p-4" data-testid="quality-comparison">
        <summary className="cursor-pointer text-sm font-medium">转换效果试听</summary>
        <p className="mt-2 text-xs text-muted-foreground">此前保存的日语录音，基础声源为 Gemini Iapetus。更换基础声音不会改变这段录音。</p>
        {qualityComparisons.map((item, index) => <div key={item.label} className="mt-3">
          <p className="text-sm">{item.label}</p>
          <audio ref={(player) => { comparisonAudio.current[index] = player; }} src={item.url} controls={!busy} preload="metadata" className="mt-2 w-full" aria-label={item.label} onPlay={(event) => {
            if (busy) event.currentTarget.pause();
            sourceBaselineAudio.current?.pause();
            referenceAudio.current?.pause();
            reijiEarlyAudio.current?.pause();
            officialComparisonAudio.current.forEach((player) => player?.pause());
            comparisonAudio.current.forEach((player) => { if (player !== event.currentTarget) player?.pause(); });
          }} />
        </div>)}
      </details>}
      <div className="mt-6 flex flex-col gap-3" aria-label="语音试验模式">
        {modes.map((entry) => <div key={entry.id} className="flex flex-col gap-1">
          <Button variant={entry.id === mode ? "default" : "outline"} disabled={!entry.available || busy} onClick={() => setMode(entry.id)} aria-pressed={entry.id === mode}>{entry.id === "gemini_vc_quality" ? `${languageLabel}对话 · 流式转换` : modeLabels[entry.id] ?? entry.label}{!entry.available ? " · 暂不可用" : ""}</Button>
          {entry.id === "gemini_vc_quality" && <p className="text-xs text-muted-foreground">Gemini Live 使用 {geminiVoiceLabel} 生成{languageLabel}，经 MeanVC2 流式转换声音。</p>}
          {entry.id === "gpt_live_native" && <p className="text-xs text-muted-foreground">连续麦克风输入与原生语音输出，默认 Echo，可选日语原生声音。真实全双工体验仍待 API 验收。</p>}
          {!entry.available && entry.reason && <p className="text-xs text-muted-foreground">{entry.reason}</p>}
        </div>)}
      </div>
      <p className="mt-4 text-sm text-muted-foreground" data-testid="local-voice-scope">{mode === "gpt_live_native" ? "GPT-Live-1 / Echo 独立原声实验，尚未接入角色变声或视频嘴型。" : mode === "gemini_vc_quality" ? "当前版本仍在验证音色相似度和连续对话效果，欢迎直接试听反馈。" : `直接听 Gemini Live 的 ${geminiVoiceLabel}，不经过变声。`}</p>
      {phase === "offline" && !isLocalPage() && <div className="mt-4"><Input aria-label="体验码" type="password" value={invite} onChange={(event) => setInvite(event.target.value)} placeholder="体验码（完整邀请链接会自动填写）" autoComplete="off" /></div>}
      <div className="mt-6 flex flex-wrap gap-3">
        {phase === "offline" ? <Button onClick={() => void connect()}>连接体验服务</Button> : <Button onClick={() => void start()} disabled={phase !== "idle" || !selected?.available || !characterAvailable || !languageAvailable || !sourceVoiceAvailable} data-testid="local-voice-start">{phase === "starting" ? "连接中…" : mode === "mic_vc" ? "开始试听" : "开始通话"}</Button>}
        {mode !== "mic_vc" && mode !== "gpt_live_native" && <Button variant="secondary" onClick={() => void start(false)} disabled={phase !== "idle" || !selected?.available || !characterAvailable || !languageAvailable || !sourceVoiceAvailable} data-testid="local-voice-text-start">先用文字试聊</Button>}
        {mode !== "gpt_live_native" && <Button variant="secondary" onClick={interrupt} disabled={phase !== "live"} data-testid="local-voice-interrupt">打断 / 清空播放</Button>}
        <Button variant="outline" onClick={stop} disabled={phase === "offline" || phase === "checking"} data-testid="local-voice-stop">结束</Button>
      </div>
      {phase === "idle" && !characterAvailable && <p className="mt-3 text-sm text-destructive">此角色暂未就绪，请稍后重新连接。</p>}
      {phase === "idle" && !languageAvailable && <p className="mt-3 text-sm text-destructive">所选语言暂未就绪，请稍后重新连接。</p>}
      {phase === "idle" && !sourceVoiceAvailable && <p className="mt-3 text-sm text-destructive">服务暂不支持所选基础声音，请刷新后重新连接。</p>}
      <p role="status" className="mt-4 text-sm" data-testid="local-voice-status">{status}</p>
      <meter className="mt-2 h-3 w-full" min={0} max={100} value={level} aria-label="麦克风输入音量" />
      {error && <p role="alert" className="mt-3 text-sm text-destructive">{error}</p>}
      {model && <p className="mt-3 text-xs text-muted-foreground">当前引擎：{model}</p>}
    </section>
    {mode !== "mic_vc" && mode !== "gpt_live_native" && <section className="rounded-2xl border border-border bg-card p-4">
      <form className="flex gap-3" onSubmit={(event) => { event.preventDefault(); if (text.trim() && ready.current) { interrupt(); send({ type: "text", text: text.trim() }); setText(""); } }}>
        <Input aria-label="文字消息" value={text} onChange={(event) => setText(event.target.value)} placeholder={`也可以输入一句${languageLabel}`} maxLength={2000} disabled={phase !== "live"} />
        <Button type="submit" disabled={phase !== "live" || !text.trim()}>发送</Button>
      </form>
      <div className="mt-4 flex max-h-64 flex-col gap-2 overflow-y-auto text-sm" aria-live="polite">{transcript.map((line, index) => <p key={index}><span className="text-muted-foreground">{line.role === "user" ? "你" : character.name}：</span>{line.text}</p>)}</div>
    </section>}
    {mode === "gpt_live_native" && <div className="flex max-h-64 flex-col gap-2 overflow-y-auto text-sm" aria-live="polite">{transcript.map((line, index) => <p key={index}><span className="text-muted-foreground">{line.role === "user" ? "你" : character.name}：</span>{line.text}</p>)}</div>}
    <details className="text-xs text-muted-foreground" aria-label="实测信息"><summary className="cursor-pointer">本次体验的等待与播放数据</summary>
      {metric && <p>{metric}</p>}
      {playbackMs !== null && <p>首个音频包抵达 → 浏览器安排播放：约 {playbackMs} ms（含 200 ms 播放缓冲；不含硬件输出延迟）</p>}
      {phase === "live" && <p>播放断流：{underruns.count} 次，累计约 {Math.round(underruns.milliseconds)} ms（浏览器调度记录）</p>}
      <p className="mt-2">实时显示本次连接的数据。云端推理、网络和本机转换共同影响等待；这不是 Prelulu 生产性能。</p>
    </details>
  </main>;
}
