import { describe, expect, it } from 'vitest';

import { estimateImageTransmitSeconds, parseImageEnvelope } from '../utils/imageEnvelope';

describe('meshcore-sar IE4 compatibility', () => {
  it('parses the reference base36 vector', () => {
    expect(parseImageEnvelope('IE4:a:0:e:74:4r:1mc')).toEqual({
      sessionId: '0000000a',
      format: 0,
      fragmentCount: 14,
      width: 256,
      height: 171,
      sizeBytes: 2100,
    });
  });

  it('rejects malformed and impossible metadata', () => {
    expect(parseImageEnvelope('IE4:a:2:e:74:4r:1mc')).toBeNull();
    expect(parseImageEnvelope('IE4:a:0:e:75:4r:1mc')).toBeNull();
    expect(parseImageEnvelope('IE4:a:0:e:74:4r:1')).toBeNull();
  });

  it('estimates a positive LoRa transfer duration', () => {
    expect(estimateImageTransmitSeconds(14, 2100)).toBeGreaterThan(0);
  });
});
