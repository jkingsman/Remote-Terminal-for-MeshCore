const TARGET_RATE = 8_000;

export interface VoiceRecording {
  pcm: Blob;
  durationMs: number;
}

export class VoiceCapture {
  private context: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private processor: ScriptProcessorNode | null = null;
  private chunks: Float32Array[] = [];
  private sampleRate = 0;
  private startedAt = 0;

  async start(): Promise<void> {
    if (!window.isSecureContext)
      throw new Error('Voice recording requires HTTPS to access your microphone.');
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      video: false,
    });
    this.context = new AudioContext();
    this.sampleRate = this.context.sampleRate;
    const source = this.context.createMediaStreamSource(this.stream);
    this.processor = this.context.createScriptProcessor(4096, 1, 1);
    this.processor.onaudioprocess = (event) =>
      this.chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
    source.connect(this.processor);
    this.processor.connect(this.context.destination);
    this.startedAt = performance.now();
  }

  async stop(): Promise<VoiceRecording> {
    const durationMs = Math.min(10_000, performance.now() - this.startedAt);
    this.processor?.disconnect();
    this.stream?.getTracks().forEach((track) => track.stop());
    await this.context?.close();
    const input = new Float32Array(this.chunks.reduce((sum, chunk) => sum + chunk.length, 0));
    let offset = 0;
    for (const chunk of this.chunks) {
      input.set(chunk, offset);
      offset += chunk.length;
    }
    const outputLength = Math.min(
      TARGET_RATE * 10,
      Math.floor((input.length * TARGET_RATE) / this.sampleRate)
    );
    const pcm = new Int16Array(outputLength);
    for (let i = 0; i < outputLength; i += 1) {
      const sample = Math.max(
        -1,
        Math.min(1, input[Math.floor((i * this.sampleRate) / TARGET_RATE)] ?? 0)
      );
      pcm[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
    }
    return { pcm: new Blob([pcm.buffer], { type: 'application/octet-stream' }), durationMs };
  }

  async cancel(): Promise<void> {
    await this.stop();
  }
}
