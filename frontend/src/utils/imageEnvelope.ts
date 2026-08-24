export type ImageFormatId = 0 | 1;

export interface ImageEnvelope {
  sessionId: string;
  format: ImageFormatId;
  fragmentCount: number;
  width: number;
  height: number;
  sizeBytes: number;
}

const MAX_FRAGMENTS = 255;
export const IMAGE_FRAGMENT_BYTES = 152;

export function parseImageEnvelope(text: string): ImageEnvelope | null {
  if (!text.startsWith('IE4:')) return null;
  const parts = text.slice(4).split(':');
  if (parts.length !== 6 || !/^[0-9a-z]{1,7}$/.test(parts[0])) return null;
  const values = parts.map((part) => Number.parseInt(part, 36));
  if (values.some((value) => !Number.isSafeInteger(value))) return null;
  const [sid, format, fragmentCount, width, height, sizeBytes] = values;
  if (
    sid < 0 ||
    sid > 0xffffffff ||
    (format !== 0 && format !== 1) ||
    fragmentCount < 1 ||
    fragmentCount > MAX_FRAGMENTS ||
    width < 1 ||
    width > 256 ||
    height < 1 ||
    height > 256 ||
    sizeBytes < 1 ||
    sizeBytes > MAX_FRAGMENTS * IMAGE_FRAGMENT_BYTES ||
    fragmentCount !== Math.ceil(sizeBytes / IMAGE_FRAGMENT_BYTES)
  ) {
    return null;
  }
  return {
    sessionId: sid.toString(16).padStart(8, '0'),
    format,
    fragmentCount,
    width,
    height,
    sizeBytes,
  };
}

export function estimateImageTransmitSeconds(
  fragmentCount: number,
  sizeBytes: number,
  pathLength = 0,
  spreadingFactor = 10,
  bandwidthHz = 250_000,
  codingRate = 5
): number {
  if (fragmentCount < 1 || sizeBytes < 1) return 0;
  const hops = Math.max(0, pathLength) + 1;
  let totalMs = 0;
  for (let index = 0; index < fragmentCount; index += 1) {
    const dataBytes = Math.min(IMAGE_FRAGMENT_BYTES, sizeBytes - index * IMAGE_FRAGMENT_BYTES);
    const payloadBytes = 2 + Math.max(0, pathLength) + 6 + dataBytes;
    const de = spreadingFactor >= 11 && bandwidthHz <= 125_000 ? 1 : 0;
    const symbolMs = (2 ** spreadingFactor / bandwidthHz) * 1000;
    const numerator = 8 * payloadBytes - 4 * spreadingFactor + 28 + 16;
    const denominator = 4 * (spreadingFactor - 2 * de);
    const coefficient = Math.max(0, Math.ceil(numerator / denominator));
    const payloadSymbols = 8 + coefficient * codingRate;
    totalMs += (12.25 * symbolMs + payloadSymbols * symbolMs) * 2 * hops;
  }
  return totalMs / 1000;
}
