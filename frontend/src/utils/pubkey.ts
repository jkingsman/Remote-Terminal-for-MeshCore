/**
 * Public key utilities for consistent handling of 64-char full keys
 * and 12-char prefixes throughout the application.
 *
 * MeshCore uses 64-character hex strings for public keys, but messages
 * and some radio operations only provide 12-character prefixes. This
 * module provides utilities for working with both formats consistently.
 */

/** Length of a full public key in hex characters */
export const PUBKEY_FULL_LENGTH = 64;

/** Length of a public key prefix in hex characters */
export const PUBKEY_PREFIX_LENGTH = 12;

/**
 * Extract the 12-character prefix from a public key.
 * Works with both full keys and existing prefixes.
 */
export function getPubkeyPrefix(key: string): string {
  return key.slice(0, PUBKEY_PREFIX_LENGTH);
}

/**
 * Check if two public keys match by comparing their prefixes.
 * This handles the case where one key is full (64 chars) and
 * the other is a prefix (12 chars).
 */
export function pubkeysMatch(a: string, b: string): boolean {
  if (!a || !b) return false;
  return getPubkeyPrefix(a) === getPubkeyPrefix(b);
}

/**
 * Check if a public key starts with the given prefix.
 * More explicit than using .startsWith() directly.
 */
export function pubkeyMatchesPrefix(fullKey: string, prefix: string): boolean {
  if (!fullKey || !prefix) return false;
  return fullKey.startsWith(prefix);
}

/**
 * Get a display name for a contact, falling back to pubkey prefix.
 */
export function getContactDisplayName(name: string | null | undefined, pubkey: string): string {
  return name || getPubkeyPrefix(pubkey);
}

/**
 * Check if a key is a full 64-character public key.
 */
export function isFullPubkey(key: string): boolean {
  return key.length === PUBKEY_FULL_LENGTH;
}

/**
 * Check if a key is a 12-character prefix.
 */
export function isPubkeyPrefix(key: string): boolean {
  return key.length === PUBKEY_PREFIX_LENGTH;
}
