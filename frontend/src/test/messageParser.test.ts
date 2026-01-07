/**
 * Tests for message parsing utilities.
 *
 * These tests verify the sender extraction logic used to parse
 * channel messages in "sender: message" format.
 */

import { describe, it, expect } from 'vitest';
import { parseSenderFromText, formatTime } from '../utils/messageParser';
import { getStateKey } from '../utils/conversationState';

describe('parseSenderFromText', () => {
  it('extracts sender and content from "sender: message" format', () => {
    const result = parseSenderFromText('Alice: Hello everyone!');

    expect(result.sender).toBe('Alice');
    expect(result.content).toBe('Hello everyone!');
  });

  it('handles sender names with spaces', () => {
    const result = parseSenderFromText('Bob Smith: How are you?');

    expect(result.sender).toBe('Bob Smith');
    expect(result.content).toBe('How are you?');
  });

  it('returns null sender for plain messages without colon-space', () => {
    const result = parseSenderFromText('Just a plain message');

    expect(result.sender).toBeNull();
    expect(result.content).toBe('Just a plain message');
  });

  it('returns null sender when colon has no space after', () => {
    const result = parseSenderFromText('Note:this is not a sender');

    expect(result.sender).toBeNull();
    expect(result.content).toBe('Note:this is not a sender');
  });

  it('rejects sender containing square brackets', () => {
    const result = parseSenderFromText('[System]: Alert message');

    expect(result.sender).toBeNull();
    expect(result.content).toBe('[System]: Alert message');
  });

  it('rejects sender containing colon', () => {
    const result = parseSenderFromText('12:30: Time announcement');

    expect(result.sender).toBeNull();
    expect(result.content).toBe('12:30: Time announcement');
  });

  it('rejects sender names longer than 50 characters', () => {
    const longName = 'A'.repeat(60);
    const result = parseSenderFromText(`${longName}: message`);

    expect(result.sender).toBeNull();
  });

  it('handles empty string', () => {
    const result = parseSenderFromText('');

    expect(result.sender).toBeNull();
    expect(result.content).toBe('');
  });

  it('handles message with multiple colons', () => {
    const result = parseSenderFromText('User: Check this URL: https://example.com');

    expect(result.sender).toBe('User');
    expect(result.content).toBe('Check this URL: https://example.com');
  });

  it('handles colon at start of message', () => {
    const result = parseSenderFromText(': no sender here');

    expect(result.sender).toBeNull();
    expect(result.content).toBe(': no sender here');
  });
});

describe('formatTime', () => {
  it('formats today timestamp as time only', () => {
    // Use current time to ensure it's "today"
    const now = Math.floor(Date.now() / 1000);

    const result = formatTime(now);

    // Should be just time (HH:MM format)
    expect(result).toMatch(/^\d{1,2}:\d{2}( [AP]M)?$/);
  });

  it('formats older timestamp with date and time', () => {
    // Use a timestamp from 2023 (definitely not today)
    const timestamp = 1700000000; // 2023-11-14

    const result = formatTime(timestamp);

    // Should contain month, day, and time
    expect(result).toMatch(/\w+ \d{1,2}/); // e.g., "Nov 14"
    expect(result).toMatch(/\d{1,2}:\d{2}/); // time portion
  });
});

describe('getStateKey', () => {
  it('creates channel state key with full id', () => {
    const key = getStateKey('channel', '5');

    expect(key).toBe('channel-5');
  });

  it('creates contact state key with 12-char prefix', () => {
    const fullKey = 'abcdef123456789012345678901234567890';
    const key = getStateKey('contact', fullKey);

    expect(key).toBe('contact-abcdef123456');
  });

  it('handles contact key shorter than 12 chars', () => {
    const shortKey = 'abc123';
    const key = getStateKey('contact', shortKey);

    expect(key).toBe('contact-abc123');
  });
});
