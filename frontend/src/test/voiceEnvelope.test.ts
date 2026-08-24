import { describe, expect, it } from 'vitest';
import { parseSenderFromText } from '../utils/messageParser';
import { parseVoiceEnvelope } from '../utils/voiceEnvelope';

describe('VE3 voice envelope', () => {
  it('parses the meshcore-sar compatibility vector', () => {
    expect(parseVoiceEnvelope('VE3:jbxb73:3:c:a')).toEqual({
      sessionId: '45abcdef',
      mode: 3,
      packetCount: 12,
      durationSeconds: 10,
    });
  });

  it('rejects malformed and over-limit messages', () => {
    expect(parseVoiceEnvelope('VE3:!:3:c:a')).toBeNull();
    expect(parseVoiceEnvelope('VE3:jbxb73:3:c:b')).toBeNull();
    expect(parseVoiceEnvelope('ordinary text')).toBeNull();
  });

  it('parses a channel envelope from the body after sender metadata is removed', () => {
    const storedText = 'Alice: VE3:jbxb73:3:c:a';
    const { sender, content } = parseSenderFromText(storedText);

    expect(sender).toBe('Alice');
    expect(content).toBe('VE3:jbxb73:3:c:a');
    expect(parseVoiceEnvelope(content)).not.toBeNull();
    expect(parseVoiceEnvelope(storedText)).toBeNull();
  });

  it('does not treat ordinary prefixed channel text as voice', () => {
    const { content } = parseSenderFromText('Alice: ordinary channel text');

    expect(parseVoiceEnvelope(content)).toBeNull();
  });
});
