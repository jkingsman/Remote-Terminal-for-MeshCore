import { describe, it, expect } from 'vitest';
import { getAvatarText, getAvatarColor, getContactAvatar, CONTACT_TYPE_REPEATER } from '../utils/contactAvatar';

describe('getAvatarText', () => {
  it('returns first emoji when name contains emoji', () => {
    expect(getAvatarText('John 🚀 Doe', 'abc123')).toBe('🚀');
    expect(getAvatarText('🎉 Party', 'abc123')).toBe('🎉');
    expect(getAvatarText('Test 😀 More 🎯', 'abc123')).toBe('😀');
  });

  it('returns initials when name has space', () => {
    expect(getAvatarText('John Doe', 'abc123')).toBe('JD');
    expect(getAvatarText('Alice Bob Charlie', 'abc123')).toBe('AB');
    expect(getAvatarText('jane smith', 'abc123')).toBe('JS');
  });

  it('returns single letter when no space', () => {
    expect(getAvatarText('John', 'abc123')).toBe('J');
    expect(getAvatarText('alice', 'abc123')).toBe('A');
  });

  it('falls back to public key when name is null', () => {
    expect(getAvatarText(null, 'abc123def456')).toBe('AB');
  });

  it('falls back to public key when name has no letters', () => {
    expect(getAvatarText('123 456', 'xyz789')).toBe('XY');
    expect(getAvatarText('---', 'def456')).toBe('DE');
  });

  it('handles space but no letter after', () => {
    expect(getAvatarText('John ', 'abc123')).toBe('J');
    expect(getAvatarText('A 123', 'abc123')).toBe('A');
  });

  it('emoji takes priority over initials', () => {
    expect(getAvatarText('John 🎯 Doe', 'abc123')).toBe('🎯');
  });
});

describe('getAvatarColor', () => {
  it('returns consistent colors for same public key', () => {
    const color1 = getAvatarColor('abc123def456');
    const color2 = getAvatarColor('abc123def456');
    expect(color1).toEqual(color2);
  });

  it('returns different colors for different public keys', () => {
    const color1 = getAvatarColor('abc123def456');
    const color2 = getAvatarColor('xyz789uvw012');
    expect(color1.background).not.toBe(color2.background);
  });

  it('returns valid HSL background color', () => {
    const color = getAvatarColor('test123');
    expect(color.background).toMatch(/^hsl\(\d+, \d+%, \d+%\)$/);
  });

  it('returns white or black text color', () => {
    const color = getAvatarColor('test123');
    expect(['#ffffff', '#000000']).toContain(color.text);
  });
});

describe('getContactAvatar', () => {
  it('returns complete avatar info', () => {
    const avatar = getContactAvatar('John Doe', 'abc123def456');
    expect(avatar.text).toBe('JD');
    expect(avatar.background).toMatch(/^hsl\(/);
    expect(['#ffffff', '#000000']).toContain(avatar.textColor);
  });

  it('handles null name', () => {
    const avatar = getContactAvatar(null, 'abc123def456');
    expect(avatar.text).toBe('AB');
  });

  it('returns repeater avatar for type=2', () => {
    const avatar = getContactAvatar('Some Repeater', 'abc123def456', CONTACT_TYPE_REPEATER);
    expect(avatar.text).toBe('🛜');
    expect(avatar.background).toBe('#444444');
    expect(avatar.textColor).toBe('#ffffff');
  });

  it('repeater avatar ignores name', () => {
    const avatar1 = getContactAvatar('🚀 Rocket', 'abc123', CONTACT_TYPE_REPEATER);
    const avatar2 = getContactAvatar(null, 'xyz789', CONTACT_TYPE_REPEATER);
    expect(avatar1.text).toBe('🛜');
    expect(avatar2.text).toBe('🛜');
    expect(avatar1.background).toBe(avatar2.background);
  });

  it('non-repeater types use normal avatar', () => {
    const avatar0 = getContactAvatar('John', 'abc123', 0);
    const avatar1 = getContactAvatar('John', 'abc123', 1);
    expect(avatar0.text).toBe('J');
    expect(avatar1.text).toBe('J');
  });
});
