/** Browser-only PCM scheduler. Cancellation stops already-scheduled sound as well as future packets. */
export class LocalVoicePlayback {
  private sources = new Set<AudioBufferSourceNode>();
  private cancelled = new Set<string>();
  private currentTurn: string | null = null;
  private nextAt = 0;
  private nextSequence = 0;
  private firstPacketAt = 0;
  private firstPlaybackTimer: ReturnType<typeof setTimeout> | undefined;

  constructor(
    private context: AudioContext,
    private onFirstPlayback: (milliseconds: number) => void,
    private onUnderrun: (gapMilliseconds: number) => void = () => {},
  ) {}

  get playing() { return this.sources.size > 0; }

  append(turn: string, encoded: string, rate: number, sequence: number) {
    if (this.cancelled.has(turn)) return;
    if (turn !== this.currentTurn) {
      this.clear();
      this.currentTurn = turn;
      this.firstPacketAt = performance.now();
      this.nextSequence = 0;
    }
    if (sequence !== this.nextSequence) throw new Error("音频顺序异常，已停止播放。");
    if (rate !== 16000 && rate !== 24000) throw new Error("不支持的音频采样率。");
    const bytes = atob(encoded);
    if (bytes.length === 0 || bytes.length % 2 || bytes.length > rate * 2 * 4) throw new Error("音频包格式异常。");
    const samples = new Float32Array(bytes.length / 2);
    for (let i = 0; i < samples.length; i++) {
      const unsigned = bytes.charCodeAt(i * 2) | bytes.charCodeAt(i * 2 + 1) << 8;
      samples[i] = (unsigned >= 32768 ? unsigned - 65536 : unsigned) / 32768;
    }
    const first = this.nextSequence === 0;
    const when = Math.max(this.nextAt, this.context.currentTime + (first ? 0.2 : 0.02));
    const gapMilliseconds = first ? 0 : Math.max(0, (when - this.nextAt) * 1000);
    // Native speech can arrive faster than playback. This is a memory bound, not
    // extra startup buffering; cancellation still stops every scheduled source.
    if (when + samples.length / rate - this.context.currentTime > 12) throw new Error("播放积压超过 12 秒，请结束后重试。");
    const buffer = this.context.createBuffer(1, samples.length, rate);
    buffer.copyToChannel(samples, 0);
    const source = this.context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.context.destination);
    this.nextAt = when + buffer.duration;
    this.nextSequence++;
    this.sources.add(source);
    source.onended = () => { this.sources.delete(source); source.disconnect(); };
    source.start(when);
    // Observe holes in the scheduled stream without counting initial buffering.
    // This is not a physical speaker measurement; ignore floating-point noise.
    if (gapMilliseconds > 0.001) this.onUnderrun(gapMilliseconds);
    if (first) {
      this.firstPlaybackTimer = setTimeout(() => {
        this.firstPlaybackTimer = undefined;
        if (this.currentTurn === turn && !this.cancelled.has(turn)) {
          this.onFirstPlayback(performance.now() - this.firstPacketAt);
        }
      }, (when - this.context.currentTime) * 1000);
    }
  }

  cancel(turn?: string) {
    if (turn) this.cancelled.add(turn);
    else if (this.currentTurn) this.cancelled.add(this.currentTurn);
    if (!turn || turn === this.currentTurn) this.clear();
  }

  private clear() {
    if (this.firstPlaybackTimer !== undefined) clearTimeout(this.firstPlaybackTimer);
    this.firstPlaybackTimer = undefined;
    for (const source of this.sources) { source.onended = null; source.stop(); source.disconnect(); }
    this.sources.clear();
    this.nextAt = 0;
    this.currentTurn = null;
  }

  dispose() { this.clear(); this.cancelled.clear(); }
}
