// Forwards mono microphone audio to the page as 16 kHz float32 chunks. The context is created at 16 kHz, so this
// is normally a straight copy; if the browser refused that rate the worklet resamples linearly.
class PttProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.target = 16000;
    this.chunk = [];
    this.chunkSize = 1280; // 80 ms at 16 kHz
  }
  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;
    let mono = input[0];
    if (sampleRate !== this.target) {
      const ratio = sampleRate / this.target;
      const n = Math.floor(mono.length / ratio);
      const out = new Float32Array(n);
      for (let i = 0; i < n; i++) {
        const pos = i * ratio, lo = Math.floor(pos), hi = Math.min(lo + 1, mono.length - 1), f = pos - lo;
        out[i] = mono[lo] * (1 - f) + mono[hi] * f;
      }
      mono = out;
    }
    let level = 0;
    for (let i = 0; i < mono.length; i++) {
      this.chunk.push(mono[i]);
      level += mono[i] * mono[i];
    }
    if (this.chunk.length >= this.chunkSize) {
      const frame = Float32Array.from(this.chunk.splice(0, this.chunkSize));
      this.port.postMessage({ frame, level: Math.sqrt(level / mono.length) }, [frame.buffer]);
    }
    return true;
  }
}
registerProcessor("ptt", PttProcessor);
