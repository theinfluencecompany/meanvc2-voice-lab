import { useEffect, useRef, useState } from "react";
import { z } from "zod";

const LocalAudio = z.string().regex(/^\.\/same-source\/[a-zA-Z0-9_./-]+\.(wav|mp3)$/);
const CaseSchema = z.object({
  id: z.string(), character: z.enum(["ivan", "reiji"]), name: z.string(),
  language: z.enum(["ja", "en"]), sourceVoice: z.literal("Schedar"),
  input: LocalAudio, reference: LocalAudio, whole: LocalAudio, stream: LocalAudio,
  sourceSeconds: z.number().positive(), wholeComputeSeconds: z.number().nonnegative(),
  streamComputeSeconds: z.number().nonnegative(), firstStreamInputSeconds: z.number().nonnegative(),
  sourceSha256: z.string().regex(/^[a-f0-9]{64}$/), referenceSha256: z.string().regex(/^[a-f0-9]{64}$/),
});
type ComparisonCase = z.infer<typeof CaseSchema>;

export function SameSourceLab() {
  const [cases, setCases] = useState<ComparisonCase[]>([]);
  const [character, setCharacter] = useState<"ivan" | "reiji">("reiji");
  const [language, setLanguage] = useState<"ja" | "en">("ja");
  const [error, setError] = useState("");
  const players = useRef<(HTMLAudioElement | null)[]>([]);
  const selected = cases.find((item) => item.character === character && item.language === language);
  const stop = () => players.current.forEach((player) => player?.pause());

  useEffect(() => {
    const controller = new AbortController();
    void fetch("./same-source.json", { cache: "no-store", signal: controller.signal }).then(async (response) => {
      if (!response.ok) throw new Error("对照录音暂时无法加载，请刷新重试。");
      const data = z.array(CaseSchema).length(4).parse(await response.json());
      if (new Set(data.map((item) => `${item.character}-${item.language}`)).size !== 4) throw new Error("对照配置不完整。");
      setCases(data);
    }).catch((cause) => { if (!controller.signal.aborted) setError(cause instanceof z.ZodError ? "对照配置无法读取。" : cause.message); });
    return () => { controller.abort(); players.current.forEach((player) => player?.pause()); };
  }, []);

  const tracks = selected ? [
    { label: "① 同一段 Gemini 声源", src: selected.input, note: "Schedar 原声，统一为 16 kHz 后同时送入两路转换。" },
    { label: "② 角色原声参考", src: selected.reference, note: "两路使用相同的角色参考和说话人特征。" },
    { label: "③ MeanVC2 整段转换", src: selected.whole, note: "官方整段推理路径，收齐整句后再转换。" },
    { label: "④ MeanVC2 流式转换", src: selected.stream, note: "当前实验的流式路径，按 160 ms 输入块回放。" },
  ] : [];

  return <main className="mx-auto flex min-h-screen max-w-3xl flex-col gap-5 px-4 py-8 text-foreground" data-testid="same-source-lab">
    <header className="flex items-center justify-between gap-4">
      <a className="font-display text-xl" href={`./${window.location.hash}`}>Prelulu</a>
      <a className="text-sm text-muted-foreground" href={`./${window.location.hash}`}>返回语音体验</a>
    </header>
    <section className="rounded-2xl border border-border bg-card p-6 shadow-e1">
      <h1 className="font-display text-2xl">整段与流式 · 同源对照</h1>
      <p className="mt-3 text-sm">用完全相同的一段声音、角色参考和模型权重，直接比较两条转换路径。</p>
      <p className="mt-2 text-xs text-muted-foreground">这里是固定录音回放。声源为 Gemini Schedar；GPT-Live／Echo 仍待有权限后的真实通话验证。</p>
      <div className="mt-5 grid gap-4 sm:grid-cols-2">
        <label className="text-sm">目标角色
          <select aria-label="同源对照角色" className="ui-input mt-2" value={character} onChange={(event) => { stop(); setError(""); setCharacter(event.target.value as "ivan" | "reiji"); }}>
            <option value="reiji">久世 怜司</option><option value="ivan">Ivan Volkov</option>
          </select>
        </label>
        <label className="text-sm">内容语言
          <select aria-label="同源对照语言" className="ui-input mt-2" value={language} onChange={(event) => { stop(); setError(""); setLanguage(event.target.value as "ja" | "en"); }}>
            <option value="ja">日语</option><option value="en">英语</option>
          </select>
        </label>
      </div>
      {!selected && !error && <p className="mt-4 text-sm" role="status">正在加载同源录音…</p>}
      {error && <p className="mt-4 text-sm text-destructive" role="alert">{error}</p>}
      <div className="mt-5 grid gap-4 sm:grid-cols-2">
        {tracks.map((track, index) => <article key={track.src} className="rounded-xl border border-border bg-background p-4">
          <h2 className="text-sm font-medium">{track.label}</h2>
          <audio ref={(player) => { players.current[index] = player; }} src={track.src} controls preload="metadata" className="mt-3 w-full" aria-label={track.label} onError={() => setError("有一段录音未加载成功，请刷新重试。")} onPlay={(event) => players.current.forEach((player) => { if (player !== event.currentTarget) player?.pause(); })} />
          <p className="mt-2 text-xs text-muted-foreground">{track.note}</p>
        </article>)}
      </div>
      <p className="mt-4 text-sm">先比较③和④是否像②，再判断是否愿意为了整段效果多等一句。播放成功和处理速度不代表音质合格。</p>
      {selected && <details className="mt-5 text-xs text-muted-foreground">
        <summary className="cursor-pointer">查看处理记录</summary>
        <p className="mt-2">输入 {selected.sourceSeconds.toFixed(2)} 秒；整段计算 {selected.wholeComputeSeconds.toFixed(2)} 秒；流式离线回放计算 {selected.streamComputeSeconds.toFixed(2)} 秒。</p>
        <p className="mt-2">流式首次产生输出前已输入 {selected.firstStreamInputSeconds.toFixed(2)} 秒音频。以上不包含云端生成、网络和播放缓冲，不能当作真实通话等待时间。</p>
        <p className="mt-2">两路使用相同权重和初始随机状态；官方与实时实现内部也有差异，因此不能仅凭两段音频把差距归因到某一个参数。</p>
        <p className="mt-2 break-all">声源 SHA256：{selected.sourceSha256}</p>
        <p className="mt-2 break-all">参考 SHA256：{selected.referenceSha256}</p>
      </details>}
    </section>
  </main>;
}
