// AudioContext requests 16 kHz; the browser resamples the microphone before this node.
// No recording is retained: send each 20 ms PCM packet immediately.
class LocalVoiceMicrophone extends AudioWorkletProcessor {
  constructor() {
    super();
    this.packet = new Int16Array(320);
    this.offset = 0;
    this.energy = 0;
  }
  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel) return true;
    for (const sample of channel) {
      const value = Math.max(-1, Math.min(1, sample));
      this.packet[this.offset++] = Math.round(value * (value < 0 ? 32768 : 32767));
      this.energy += value * value;
      if (this.offset === this.packet.length) {
        const pcm = this.packet.buffer;
        this.port.postMessage({ pcm, rms: Math.sqrt(this.energy / this.offset) }, [pcm]);
        this.packet = new Int16Array(320);
        this.offset = 0;
        this.energy = 0;
      }
    }
    return true;
  }
}
registerProcessor("local-voice-microphone", LocalVoiceMicrophone);
