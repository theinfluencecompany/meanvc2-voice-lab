import { useEffect, useRef, useState } from "react";
import { z } from "zod";

const AudioUrl = z.string().regex(/^\.\/fish-models\/[a-zA-Z0-9_.-]+\.mp3$/);
const SampleSchema = z.object({
  id: z.string().min(1),
  role: z.enum(["ivan", "love"]),
  name: z.string().min(1),
  model: z.enum(["s2-pro", "s2.1-pro", "drama-3"]),
  condition: z.enum(["normal", "whisper"]),
  language: z.literal("ja"),
  spokenText: z.string().min(1),
  text: z.string().min(1),
  audioUrl: AudioUrl,
  referenceUrl: AudioUrl,
  generationMs: z.number().nonnegative(),
  requestedModel: z.string().min(1),
  servedModel: z.null(),
}).refine((sample) => sample.requestedModel === sample.model, { message: "Requested model does not match sample label" })
  .refine((sample) => sample.text === `${sample.condition === "whisper" ? "[whisper] " : ""}${sample.spokenText}`, { message: "Sample text does not match the shared phrase" });
type FishSample = z.infer<typeof SampleSchema>;
const modelNames = { "s2-pro": "S2 Pro · 配置基线", "s2.1-pro": "S2.1 Pro", "drama-3": "Drama 3" } as const;
const conditions = ["normal", "whisper"] as const;
const models = ["s2-pro", "s2.1-pro", "drama-3"] as const;

export function FishModelLab() {
  const [samples, setSamples] = useState<FishSample[]>([]);
  const [role, setRole] = useState<"ivan" | "love">("ivan");
  const [loadError, setLoadError] = useState("");
  const [audioError, setAudioError] = useState("");
  const players = useRef(new Map<string, HTMLAudioElement>());
  const selected = samples.filter((sample) => sample.role === role);
  const reference = selected[0];
  const stop = () => players.current.forEach((player) => player.pause());
  const playOnly = (current: HTMLAudioElement) => players.current.forEach((player) => {
    if (player !== current) player.pause();
  });

  useEffect(() => {
    const controller = new AbortController();
    void fetch("./fish-models.json", { cache: "no-store", signal: controller.signal }).then(async (response) => {
      if (!response.ok) throw new Error("试听录音暂时无法加载，请刷新重试。");
      const data = z.array(SampleSchema).length(12).parse(await response.json());
      const combinations = new Set(data.map((sample) => `${sample.role}-${sample.model}-${sample.condition}`));
      if (combinations.size !== 12 || new Set(data.map((sample) => sample.id)).size !== 12) {
        throw new Error("试听配置不完整，请稍后刷新。");
      }
      if (new Set(data.map((sample) => sample.spokenText)).size !== 1) {
        throw new Error("试听台词不一致，暂时无法展示同台词对照。");
      }
      for (const character of ["ivan", "love"] as const) {
        if (new Set(data.filter((sample) => sample.role === character).map((sample) => sample.referenceUrl)).size !== 1) {
          throw new Error("角色原声配置不一致，请稍后刷新。");
        }
      }
      setSamples(data);
    }).catch((cause: unknown) => {
      if (controller.signal.aborted) return;
      setLoadError(cause instanceof z.ZodError ? "试听配置无法读取。" : cause instanceof Error ? cause.message : "试听录音暂时无法加载。");
    });
    const registeredPlayers = players.current;
    return () => { controller.abort(); registeredPlayers.forEach((player) => player.pause()); };
  }, []);

  function player(id: string, src: string, label: string) {
    return <audio
      key={src}
      ref={(element) => { if (element) players.current.set(id, element); else players.current.delete(id); }}
      src={src}
      controls
      preload="metadata"
      className="mt-3 w-full"
      aria-label={label}
      onPlay={(event) => playOnly(event.currentTarget)}
      onError={() => setAudioError(`${label}暂时无法播放，请刷新重试。`)}
    />;
  }

  return <main className="mx-auto flex min-h-screen max-w-5xl flex-col gap-5 px-4 py-8 text-foreground" data-testid="fish-model-lab">
    <header className="flex items-center justify-between gap-4">
      <a className="font-display text-xl" href={`./${window.location.hash}`}>Prelulu</a>
      <a className="text-sm text-muted-foreground" href={`./${window.location.hash}`}>返回语音体验</a>
    </header>
    <section className="rounded-2xl border border-border bg-card p-6 shadow-e1">
      <h1 className="font-display text-2xl">Fish 新版日语对照</h1>
      <p className="mt-3 text-sm">同一角色、同一段日语，比较 S2 Pro 配置基线、S2.1 Pro 与 Drama 3 的普通说话和耳语指令。</p>
      <p className="mt-2 text-xs text-muted-foreground">三路均由 Fish 直接生成角色语音，不经过 MeanVC2。这里播放固定录音，保留原始音量，未做均衡或降噪。</p>

      <label className="mt-5 block max-w-sm text-sm">角色
        <select aria-label="Fish 对照角色" className="ui-input mt-2" value={role} onChange={(event) => {
          stop(); setAudioError(""); setRole(event.target.value as "ivan" | "love");
        }}>
          <option value="ivan">Ivan Volkov</option>
          <option value="love">月見 灯</option>
        </select>
      </label>

      {!reference && !loadError && <p className="mt-4 text-sm" role="status">正在加载试听录音…</p>}
      {loadError && <p className="mt-4 text-sm text-destructive" role="alert">{loadError}</p>}
      {audioError && <p className="mt-4 text-sm text-destructive" role="alert">{audioError}</p>}

      {reference && <>
        <article className="mt-5 rounded-xl border border-border bg-background p-4">
          <h2 className="text-sm font-medium">{reference.name} · 角色原声参考</h2>
          {player(`${role}-reference`, reference.referenceUrl, `${reference.name}角色原声参考`)}
          <p className="mt-2 text-xs text-muted-foreground">用来辨认角色音色；原声参考与下面的日语录音台词不同。</p>
        </article>

        <div className="mt-5 rounded-xl bg-background p-4">
          <p className="text-xs text-muted-foreground">六段使用同一句日语；耳语版仅增加耳语指令。</p>
          <p className="mt-2 text-sm" lang="ja">{reference.spokenText}</p>
        </div>

        {conditions.map((condition) => <section key={condition} className="mt-6" aria-label={condition === "normal" ? "普通说话对照" : "耳语对照"}>
          <h2 className="text-base font-medium">{condition === "normal" ? "普通说话" : "耳语指令"}</h2>
          <div className="mt-3 grid gap-4 lg:grid-cols-3">
            {models.map((model) => {
              const sample = selected.find((item) => item.model === model && item.condition === condition)!;
              const label = `${reference.name} · ${modelNames[model]} · ${condition === "normal" ? "普通说话" : "耳语"}`;
              return <article key={sample.id} className="rounded-xl border border-border bg-background p-4">
                <h3 className="text-sm font-medium">{modelNames[model]}</h3>
                {player(sample.id, sample.audioUrl, label)}
              </article>;
            })}
          </div>
        </section>)}

        <details className="mt-6 text-xs text-muted-foreground">
          <summary className="cursor-pointer">查看生成说明与耗时</summary>
          <p className="mt-3">模型名称来自请求时明确指定的 model；后台没有回传实际服务模型 ID。播放器使用生成后保存的原始录音。</p>
          <p className="mt-2">S2 Pro 是这两个角色本轮读回的音色配置基线；生产通话的实验分流没有在本页验证。</p>
          <p className="mt-2">普通版不加风格指令；耳语版在同一句正文前加 <code>[whisper]</code>。是否保持角色声音、日语是否自然、是否像耳语，请直接试听判断。</p>
          <div className="mt-3 grid gap-1">
            {selected.map((sample) => <p key={sample.id}>{modelNames[sample.model]} · {sample.condition === "normal" ? "普通说话" : "耳语"}：{(sample.generationMs / 1000).toFixed(2)} 秒</p>)}
          </div>
          <p className="mt-2">以上为生成完整音频的耗时，不是首个声音返回时间，也不是实际通话等待时间。</p>
        </details>
      </>}
    </section>
  </main>;
}
