export interface VoiceEnvelope {
  sessionId: string;
  mode: number;
  packetCount: number;
  durationSeconds: number;
}

const VE3 = /^VE3:([0-9a-z]{1,7}):([0-9a-z]+):([0-9a-z]+):([0-9a-z]+)$/i;

export function parseVoiceEnvelope(text: string): VoiceEnvelope | null {
  const match = VE3.exec(text);
  if (!match) return null;
  const sid = Number.parseInt(match[1], 36);
  const mode = Number.parseInt(match[2], 36);
  const packetCount = Number.parseInt(match[3], 36);
  const durationSeconds = Number.parseInt(match[4], 36);
  if (
    !Number.isSafeInteger(sid) ||
    sid > 0xffffffff ||
    mode < 0 ||
    mode > 6 ||
    packetCount < 1 ||
    packetCount > 255 ||
    durationSeconds < 0 ||
    durationSeconds > 10
  )
    return null;
  return { sessionId: sid.toString(16).padStart(8, '0'), mode, packetCount, durationSeconds };
}
