import { z } from "zod";

const AudioComparisonSchema = z.object({
  characterId: z.enum(["ivan", "saku", "koharu", "love", "reiji"]),
  language: z.enum(["ja", "en"]),
  sourceVoice: z.enum(["Schedar", "Iapetus", "Kore"]).optional(),
  sameText: z.boolean(),
  transcript: z.string().max(10000).optional(),
  provenanceNote: z.string().max(2000),
  audio: z.tuple([
    z.object({ audioRelativeUrl: z.string(), label: z.string().max(200), transcript: z.string().max(10000).optional() }),
    z.object({ audioRelativeUrl: z.string(), label: z.string().max(200), transcript: z.string().max(10000).optional() }),
  ]),
});
export type AudioComparison = z.infer<typeof AudioComparisonSchema>;

export async function loadAudioComparisons(signal: AbortSignal): Promise<AudioComparison[]> {
  const response = await fetch("./comparison.json", { cache: "no-store", signal });
  if (!response.ok) return [];
  const parsed = z.array(AudioComparisonSchema).max(10).safeParse(await response.json());
  if (!parsed.success) return [];
  const verified = await Promise.all(parsed.data.map(async (comparison) => {
    try {
      const playable = await Promise.all(comparison.audio.map(async (item) => {
        if (!item.audioRelativeUrl.startsWith("./") && !item.audioRelativeUrl.startsWith("/")) return false;
        const url = new URL(item.audioRelativeUrl, window.location.href);
        if (url.origin !== window.location.origin || url.search || url.hash || !/\.(wav|mp3|ogg|m4a)$/i.test(url.pathname)) return false;
        const asset = await fetch(url, { method: "HEAD", cache: "no-store", signal });
        const type = asset.headers.get("content-type") ?? "";
        return asset.ok && (type.startsWith("audio/") || type.startsWith("application/octet-stream"));
      }));
      return playable.every(Boolean) ? comparison : null;
    } catch { return null; }
  }));
  return verified.filter((entry): entry is AudioComparison => entry !== null);
}
