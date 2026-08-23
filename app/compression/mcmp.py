"""Python port of meshcore-open's MCMP text compressor (v2 + v3 container).

MCMP ("Mesh Compress") packs chat text so a much longer message still fits in a
single LoRa packet. It is a binary arithmetic coder driven by a bundled PPM /
context-mixing n-gram language model (``model-en-ru.json``); the coded bitstream
is repacked into printable characters with basE91 and carried inside an ordinary
message body behind a short prefix:

    mcmp2:<marker><base91>   -- v2 text transport
    mcmp3:<base91>           -- v3 container (timestamp / sender / reply / body)

This module is a faithful port of meshcore-open (branch
``origin/rename-mco-advanced``):

    lib/helpers/mesh_compressor.dart   -> the v2 arithmetic coder + model
    lib/helpers/mcmp_app_codec.dart    -> the v3 container + its basE91 variant

so it is wire-compatible with that implementation (and, transitively, with
dimapanov/mesh-compressor, whose "official" transport samples the port is tested
against). v1 (``mcmp:``) is intentionally omitted: meshcore-open disabled it and
stopped bundling its model.

**v3 signatures (Ed25519) are NOT implemented here.** Signing requires the radio
firmware's sign command, so this module encodes v3 *unsigned* and, when decoding
a *signed* v3 message from a peer, extracts the text and skips signature
verification (the signature bytes are read past, never checked). Everything else
in v3 — timestamp, sender name, reply anchor — is pure app-side and supported.

Bit-exactness note: arithmetic coding requires the decoder to rebuild the exact
same symbol probabilities the encoder used. The CDF blend below mirrors the Dart
float pipeline operation-for-operation; correctness is pinned by golden
wire<->text vectors in ``tests/test_mcmp.py``. Decode is deliberately lenient
(it does not re-encode-verify like Dart does) so a message from a peer on a
different libm still displays rather than being rejected.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading

logger = logging.getLogger(__name__)

# --- Framing / algorithm constants (mesh_compressor.dart) ---------------------

_BOS = "\x02"
_EOF = "\x03"
_ESC = "\x04"

_CDF_SCALE = 1 << 20
_PRECISION = 32
_FULL = 1 << _PRECISION
_HALF = 1 << (_PRECISION - 1)
_QUARTER = 1 << (_PRECISION - 2)
_THREE_QUARTER = 3 * _QUARTER
_MASK = _FULL - 1

_SCRIPT_BOOST = 8
_ESC_PROB = 500
_CDF_CACHE_MAX = 50000
_DECODE_HARD_LIMIT = 4096

_PREFIX_V2 = "mcmp2:"
_PREFIX_V3 = "mcmp3:"

_TEXT_EMPTY_MARKER = "!"
_TEXT_COMPRESSED_NO_ESC_MARKER = '"'
_TEXT_COMPRESSED_ESC_MARKER = "#"
_TEXT_MARKERS = frozenset(
    {_TEXT_EMPTY_MARKER, _TEXT_COMPRESSED_NO_ESC_MARKER, _TEXT_COMPRESSED_ESC_MARKER}
)

# basE91 alphabet, identical in both Dart helpers. Note it excludes space, the
# apostrophe, the backslash and the hyphen; the closing double-quote is symbol 90.
_BASE91_ALPHABET = (
    'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!#$%&()*+,./:;<=>?@[]^_`{|}~"'
)
_BASE91_DECODE = {ch: i for i, ch in enumerate(_BASE91_ALPHABET)}

# v2 escape sub-model blocks: (block_id, start, end) inclusive.
_UNICODE_BLOCKS = (
    (0, 0x0400, 0x04FF),
    (1, 0x0100, 0x024F),
    (2, 0x2000, 0x206F),
    (3, 0x2190, 0x21FF),
    (4, 0x2600, 0x27BF),
    (5, 0x1F300, 0x1F5FF),
    (6, 0x1F600, 0x1F64F),
    (7, 0x1F900, 0x1F9FF),
    (8, 0xFE00, 0xFE0F),
    (9, 0x1FA70, 0x1FAFF),
)
_NUM_BLOCKS = 10
_FALLBACK_BLOCK_ID = _NUM_BLOCKS
_TOTAL_BLOCK_IDS = _NUM_BLOCKS + 1

_MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "model-en-ru.json")


class MeshCompressorError(Exception):
    """Raised when a payload cannot be decoded as MCMP."""


def _char_script(cp: int) -> str:
    """Classify a codepoint into the coarse script buckets the model uses."""
    if cp < 0x0041:
        return "Common"
    if cp <= 0x024F or (0x1E00 <= cp <= 0x1EFF):
        return "Latin"
    if 0x0400 <= cp <= 0x052F:
        return "Cyrillic"
    if cp > 0xFFFF:
        return "Common"
    return "Other"


# --- Arithmetic coder ---------------------------------------------------------


class _ArithmeticEncoder:
    __slots__ = ("low", "high", "pending", "bits")

    def __init__(self) -> None:
        self.low = 0
        self.high = _MASK
        self.pending = 0
        self.bits: list[int] = []

    def encode_symbol(self, low_count: int, high_count: int, total: int) -> None:
        rng = self.high - self.low + 1
        self.high = self.low + (rng * high_count) // total - 1
        self.low = self.low + (rng * low_count) // total

        while True:
            if self.high < _HALF:
                self._emit_bit(0)
            elif self.low >= _HALF:
                self._emit_bit(1)
                self.low -= _HALF
                self.high -= _HALF
            elif self.low >= _QUARTER and self.high < _THREE_QUARTER:
                self.pending += 1
                self.low -= _QUARTER
                self.high -= _QUARTER
            else:
                break
            self.low = (self.low << 1) & _MASK
            self.high = ((self.high << 1) | 1) & _MASK

    def finish_bits(self) -> list[int]:
        self.pending += 1
        self._emit_bit(0 if self.low < _QUARTER else 1)
        return self.bits

    def _emit_bit(self, bit: int) -> None:
        self.bits.append(bit)
        opposite = 1 - bit
        for _ in range(self.pending):
            self.bits.append(opposite)
        self.pending = 0


class _ArithmeticDecoder:
    __slots__ = ("data", "total_bits", "low", "high", "value", "bit_pos")

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.total_bits = len(data) * 8
        self.low = 0
        self.high = _MASK
        self.value = 0
        self.bit_pos = 0
        for _ in range(_PRECISION):
            self.value = (self.value << 1) | self._read_bit()

    def _read_bit(self) -> int:
        if self.bit_pos >= self.total_bits:
            return 0
        byte_index = self.bit_pos >> 3
        bit_index = 7 - (self.bit_pos & 7)
        self.bit_pos += 1
        return (self.data[byte_index] >> bit_index) & 1

    def _renormalize(self) -> None:
        while True:
            if self.high < _HALF:
                pass
            elif self.low >= _HALF:
                self.low -= _HALF
                self.high -= _HALF
                self.value -= _HALF
            elif self.low >= _QUARTER and self.high < _THREE_QUARTER:
                self.low -= _QUARTER
                self.high -= _QUARTER
                self.value -= _QUARTER
            else:
                break
            self.low = (self.low << 1) & _MASK
            self.high = ((self.high << 1) | 1) & _MASK
            self.value = ((self.value << 1) | self._read_bit()) & _MASK

    def decode_model_index(self, highs: list[int]) -> int:
        """Decode one symbol against the model CDF (cumulative ``highs``)."""
        total = _CDF_SCALE
        rng = self.high - self.low + 1
        scaled = ((self.value - self.low + 1) * total - 1) // rng

        left = 0
        right = len(highs) - 1
        while left < right:
            mid = (left + right) >> 1
            if highs[mid] <= scaled:
                left = mid + 1
            else:
                right = mid

        entry_high = highs[left]
        entry_low = highs[left - 1] if left > 0 else 0
        self.high = self.low + (rng * entry_high) // total - 1
        self.low = self.low + (rng * entry_low) // total
        self._renormalize()
        return left

    def decode_uniform(self, count: int) -> int:
        """Decode one symbol from a uniform distribution of ``count`` symbols."""
        rng = self.high - self.low + 1
        scaled = ((self.value - self.low + 1) * count - 1) // rng
        index = scaled if scaled < count else count - 1
        if index < 0:
            index = 0
        self.high = self.low + (rng * (index + 1)) // count - 1
        self.low = self.low + (rng * index) // count
        self._renormalize()
        return index


# --- Model --------------------------------------------------------------------


class _MeshModel:
    """The n-gram model plus the PPM CDF blend, ported from Dart."""

    def __init__(self, order: int, vocab: list[str], counts: list[dict]) -> None:
        self.order = order
        self.vocab = vocab
        self.vocab_set = set(vocab)
        self.vocab_index = {ch: i for i, ch in enumerate(vocab)}
        self.counts = counts
        self.totals = [
            {ctx: sum(sym_counts.values()) for ctx, sym_counts in level.items()} for level in counts
        ]
        self.char_scripts = {ch: _char_script(ord(ch)) for ch in vocab}
        self._cdf_cache: dict[tuple[bool, str], list[int]] = {}

    @classmethod
    def from_dict(cls, data: dict) -> _MeshModel:
        order = int(data["o"])
        vocab = list(data["v"])
        for symbol in (_EOF, _ESC):
            if symbol not in vocab:
                vocab.append(symbol)
        # Dart sorts with String.compareTo, i.e. by UTF-16 code unit. Encoding
        # each symbol as big-endian UTF-16 and comparing the raw bytes
        # reproduces that ordering exactly (supplementary emoji sort by their
        # leading surrogate, not their codepoint) -- which is what every
        # symbol's cumulative CDF position depends on.
        vocab.sort(key=lambda s: s.encode("utf-16-be"))

        raw_counts = data["c"]
        counts = []
        for n in range(order + 1):
            level = raw_counts[n]
            counts.append(
                {
                    ctx: {sym: int(cnt) for sym, cnt in sym_counts.items()}
                    for ctx, sym_counts in level.items()
                }
            )
        return cls(order=order, vocab=vocab, counts=counts)

    def get_cdf(self, context: str, has_escapes: bool) -> list[int]:
        key = (has_escapes, context)
        cached = self._cdf_cache.get(key)
        if cached is not None:
            return cached
        cdf = self._compute_cdf(context, has_escapes)
        if len(self._cdf_cache) < _CDF_CACHE_MAX:
            self._cdf_cache[key] = cdf
        return cdf

    def _compute_cdf(self, context: str, has_escapes: bool) -> list[int]:
        active: list[tuple[int, str, int, float]] = []
        total_weight = 0.0
        max_match_order = -1

        for n in range(self.order, -1, -1):
            ctx = context[-n:] if n > 0 else ""
            total = self.totals[n].get(ctx, 0)
            if total <= 0:
                continue
            confidence = total / (total + 1.5)
            weight = ((n + 1) ** 3) * math.log(total + 1) * confidence
            active.append((n, ctx, total, weight))
            total_weight += weight
            if n > max_match_order:
                max_match_order = n

        effective_boost = _SCRIPT_BOOST * 4 if max_match_order <= 2 else _SCRIPT_BOOST

        # Nearest non-Common script in the context (scanning from the end).
        context_script: str | None = None
        for ch in reversed(context):
            if ch == _BOS:
                continue
            context_script = self.char_scripts.get(ch) or _char_script(ord(ch))
            if context_script != "Common":
                break

        compat_scripts: set[str] | None = None
        if context_script is not None and context_script != "Common":
            compat_scripts = {context_script, "Common"}

        vocab = self.vocab
        freqs = [0] * len(vocab)
        epsilon_total = 0
        for i, ch in enumerate(vocab):
            ch_script = self.char_scripts.get(ch, "Other")
            if ch == _ESC:
                epsilon = _ESC_PROB if has_escapes else 0
            elif compat_scripts is not None and ch_script in compat_scripts:
                epsilon = effective_boost
            elif ch_script == "Common":
                epsilon = max(1, effective_boost // 3)
            else:
                epsilon = 1
            freqs[i] = epsilon
            epsilon_total += epsilon

        if epsilon_total > _CDF_SCALE // 2:
            scale_factor = (_CDF_SCALE // 2) / epsilon_total
            epsilon_total = 0
            for i in range(len(freqs)):
                freqs[i] = max(1, int(freqs[i] * scale_factor))
                epsilon_total += freqs[i]

        if total_weight > 0:
            scale = _CDF_SCALE - epsilon_total
            for n, ctx, total, weight in active:
                counts_for_context = self.counts[n].get(ctx)
                if counts_for_context is None:
                    continue
                factor = (weight / total_weight) * scale / total
                for sym, value in counts_for_context.items():
                    index = self.vocab_index.get(sym)
                    if index is None:
                        continue
                    freqs[index] += int(value * factor)

        total = sum(freqs)
        if total != _CDF_SCALE:
            diff = _CDF_SCALE - total
            if diff > 0:
                max_index = 0
                for i in range(1, len(freqs)):
                    if freqs[i] > freqs[max_index]:
                        max_index = i
                freqs[max_index] += diff
            else:
                # Trim the largest buckets first. Python's sort is stable, so
                # ties keep ascending-index order (matching the JS reference the
                # Dart port tracks).
                indices = sorted(range(len(freqs)), key=lambda i: freqs[i], reverse=True)
                remaining = -diff
                for index in indices:
                    if remaining <= 0:
                        break
                    can_remove = freqs[index] - 1
                    remove = min(can_remove, remaining)
                    freqs[index] -= remove
                    remaining -= remove

        highs = []
        cumulative = 0
        for freq in freqs:
            cumulative += freq
            highs.append(cumulative)
        return highs


# --- base91 (two variants: v2 mesh_compressor, v3 mcmp_app_codec) -------------


def _b91_encode_v2(data: bytes) -> str:
    if not data:
        return ""
    out: list[str] = []
    n = 0
    n_bits = 0
    for byte in data:
        n |= byte << n_bits
        n_bits += 8
        if n_bits > 13:
            value = n & 8191
            if value > 88:
                n >>= 13
                n_bits -= 13
            else:
                value = n & 16383
                n >>= 14
                n_bits -= 14
            out.append(_BASE91_ALPHABET[value % 91])
            out.append(_BASE91_ALPHABET[value // 91])
    if n_bits > 0:
        out.append(_BASE91_ALPHABET[n % 91])
        if n >= 91 or n_bits > 7:
            out.append(_BASE91_ALPHABET[n // 91])
    return "".join(out)


def _b91_decode_v2(text: str) -> bytes:
    if not text:
        return b""
    out = bytearray()
    n = 0
    n_bits = 0
    value = -1
    for ch in text:
        decoded = _BASE91_DECODE.get(ch)
        if decoded is None:
            raise MeshCompressorError(f"Invalid Base91 character: {ch!r}")
        if value == -1:
            value = decoded
        else:
            value += decoded * 91
            bit_count = 13 if (value & 8191) > 88 else 14
            n |= value << n_bits
            n_bits += bit_count
            value = -1
            while n_bits >= 8:
                out.append(n & 0xFF)
                n >>= 8
                n_bits -= 8
    if value != -1:
        n |= value << n_bits
        n_bits += 7
        while n_bits >= 8:
            out.append(n & 0xFF)
            n >>= 8
            n_bits -= 8
    return bytes(out)


def _b91_encode_v3(data: bytes) -> str:
    out: list[str] = []
    b = 0
    n = 0
    for byte in data:
        b |= byte << n
        n += 8
        if n > 13:
            value = b & 8191
            if value > 88:
                b >>= 13
                n -= 13
            else:
                value = b & 16383
                b >>= 14
                n -= 14
            out.append(_BASE91_ALPHABET[value % 91])
            out.append(_BASE91_ALPHABET[value // 91])
    if n != 0:
        out.append(_BASE91_ALPHABET[b % 91])
        if n > 7 or b > 90:
            out.append(_BASE91_ALPHABET[b // 91])
    return "".join(out)


def _b91_decode_v3(text: str) -> bytes:
    out = bytearray()
    b = 0
    n = 0
    v = -1
    for ch in text:
        decoded = _BASE91_DECODE.get(ch)
        if decoded is None:
            raise MeshCompressorError(f"Invalid Base91 character: {ch!r}")
        if v < 0:
            v = decoded
        else:
            v += decoded * 91
            b |= v << n
            n += 13 if (v & 8191) > 88 else 14
            while True:
                out.append(b & 0xFF)
                b >>= 8
                n -= 8
                if n <= 7:
                    break
            v = -1
    if v >= 0:
        out.append((b | (v << n)) & 0xFF)
    return bytes(out)


# --- bit / context helpers ----------------------------------------------------


def _bits_to_bytes(bits: list[int]) -> bytes:
    if not bits:
        return b""
    out = bytearray((len(bits) + 7) >> 3)
    for i, bit in enumerate(bits):
        if bit:
            out[i >> 3] |= 1 << (7 - (i & 7))
    return bytes(out)


def _bits_to_min_bytes(bits: list[int]) -> bytes:
    out = _bits_to_bytes(bits)
    end = len(out)
    while end > 0 and out[end - 1] == 0:
        end -= 1
    return out[:end]


def _append_context(context: str, ch: str, order: int) -> str:
    combined = context + ch
    if len(combined) <= order:
        return combined
    return combined[-order:]


# --- codepoint escape sub-model (v2) -----------------------------------------


def _encode_codepoint(encoder: _ArithmeticEncoder, cp: int) -> None:
    for block_id, start, end in _UNICODE_BLOCKS:
        if start <= cp <= end:
            encoder.encode_symbol(block_id, block_id + 1, _TOTAL_BLOCK_IDS)
            offset = cp - start
            encoder.encode_symbol(offset, offset + 1, end - start + 1)
            return
    encoder.encode_symbol(_FALLBACK_BLOCK_ID, _FALLBACK_BLOCK_ID + 1, _TOTAL_BLOCK_IDS)
    encoder.encode_symbol(cp & 0x7F, (cp & 0x7F) + 1, 128)
    encoder.encode_symbol((cp >> 7) & 0x7F, ((cp >> 7) & 0x7F) + 1, 128)
    encoder.encode_symbol((cp >> 14) & 0x7F, ((cp >> 14) & 0x7F) + 1, 128)


def _decode_codepoint(decoder: _ArithmeticDecoder) -> int:
    block_id = decoder.decode_uniform(_TOTAL_BLOCK_IDS)
    if block_id < _NUM_BLOCKS:
        _, start, end = _UNICODE_BLOCKS[block_id]
        offset = decoder.decode_uniform(end - start + 1)
        return start + offset
    b0 = decoder.decode_uniform(128)
    b1 = decoder.decode_uniform(128)
    b2 = decoder.decode_uniform(128)
    return b0 | (b1 << 7) | (b2 << 14)


# --- core compress / decompress ----------------------------------------------


def _compress_arithmetic_bits(text: str, model: _MeshModel) -> tuple[int, list[int]]:
    has_extras = any(ch not in model.vocab_set for ch in text)
    encoder = _ArithmeticEncoder()
    context = _BOS * model.order

    for ch in text:
        highs = model.get_cdf(context, has_extras)
        if ch in model.vocab_set:
            index = model.vocab_index[ch]
            low = highs[index - 1] if index > 0 else 0
            encoder.encode_symbol(low, highs[index], _CDF_SCALE)
        else:
            esc_index = model.vocab_index[_ESC]
            esc_low = highs[esc_index - 1] if esc_index > 0 else 0
            encoder.encode_symbol(esc_low, highs[esc_index], _CDF_SCALE)
            _encode_codepoint(encoder, ord(ch))
        context = _append_context(context, ch, model.order)

    highs = model.get_cdf(context, has_extras)
    eof_index = model.vocab_index[_EOF]
    eof_low = highs[eof_index - 1] if eof_index > 0 else 0
    encoder.encode_symbol(eof_low, highs[eof_index], _CDF_SCALE)

    return (1 if has_extras else 0), encoder.finish_bits()


def _decode_arithmetic(ac_data: bytes, model: _MeshModel, has_escapes: bool) -> str:
    decoder = _ArithmeticDecoder(ac_data)
    context = _BOS * model.order
    out: list[str] = []

    for _ in range(_DECODE_HARD_LIMIT):
        highs = model.get_cdf(context, has_escapes)
        index = decoder.decode_model_index(highs)
        ch = model.vocab[index]
        if ch == _EOF:
            break
        if ch == _ESC and has_escapes:
            ch = chr(_decode_codepoint(decoder))
        out.append(ch)
        context = _append_context(context, ch, model.order)

    return "".join(out)


class MeshCompressor:
    """v2 arithmetic text compressor. Load once, reuse (thread-safe reads)."""

    def __init__(self) -> None:
        self._model: _MeshModel | None = None

    @property
    def is_ready(self) -> bool:
        return self._model is not None

    def load_from_dict(self, data: dict) -> None:
        self._model = _MeshModel.from_dict(data)

    def load_from_path(self, path: str = _MODEL_PATH) -> None:
        with open(path, encoding="utf-8") as fh:
            self.load_from_dict(json.load(fh))

    def _require_model(self) -> _MeshModel:
        model = self._model
        if model is None:
            raise MeshCompressorError("MeshCompressor model is not initialized")
        return model

    # -- v2 text transport ("mcmp2:") --

    def has_prefix(self, text: str) -> bool:
        stripped = text.lstrip()
        return stripped.startswith(_PREFIX_V2) and len(stripped) > len(_PREFIX_V2)

    def encode_if_smaller(self, text: str) -> str:
        model = self._model
        if model is None or not text or text.startswith(_PREFIX_V2):
            return text
        try:
            encoded = self._compress_text_transport(text, model)
            candidate = f"{_PREFIX_V2}{encoded}"
            if len(candidate.encode("utf-8")) < len(text.encode("utf-8")):
                return candidate
        except Exception:
            pass
        return text

    def try_decode_prefixed(self, text: str) -> str | None:
        stripped = text.lstrip()
        if stripped.startswith(_PREFIX_V2) and len(stripped) > len(_PREFIX_V2):
            model = self._model
            if model is None:
                return None
            try:
                return self._decompress_text_transport(stripped[len(_PREFIX_V2) :], model)
            except Exception:
                return None
        return None

    def _compress_text_transport(self, text: str, model: _MeshModel) -> str:
        if not text:
            return _TEXT_EMPTY_MARKER
        flags, bits = _compress_arithmetic_bits(text, model)
        payload = _bits_to_min_bytes(bits)
        marker = (
            _TEXT_COMPRESSED_ESC_MARKER if (flags & 0x01) == 1 else _TEXT_COMPRESSED_NO_ESC_MARKER
        )
        compressed_text = f"{marker}{_b91_encode_v2(payload)}"
        if len(compressed_text) >= len(text) and text[0] not in _TEXT_MARKERS:
            return text
        return compressed_text

    def _decompress_text_transport(self, text: str, model: _MeshModel) -> str:
        if not text:
            return ""
        head = text[0]
        if head == _TEXT_EMPTY_MARKER:
            return ""
        if head == _TEXT_COMPRESSED_NO_ESC_MARKER:
            has_escapes = False
        elif head == _TEXT_COMPRESSED_ESC_MARKER:
            has_escapes = True
        else:
            return text
        payload = _b91_decode_v2(text[1:])
        return _decode_arithmetic(payload, model, has_escapes)

    # -- binary form (used inside the v3 container) --

    def compress_to_bytes(self, text: str) -> bytes:
        model = self._require_model()
        if not text:
            return b""
        utf8_bytes = text.encode("utf-8")
        flags, bits = _compress_arithmetic_bits(text, model)
        ac_result = bytes([flags]) + _bits_to_bytes(bits)
        if len(ac_result) > len(utf8_bytes) and utf8_bytes[0] >= 0x02:
            return utf8_bytes
        return ac_result

    def decompress_bytes(self, data: bytes) -> str:
        """Lenient decode of the binary form (no re-encode verification).

        Dart re-compresses and byte-compares to validate; we skip that so a
        message encoded on a peer with a slightly different libm still decodes
        rather than being rejected. For valid data the result is identical.
        """
        model = self._require_model()
        if not data:
            return ""
        first = data[0]
        if first > 0x01:
            return data.decode("utf-8")
        has_escapes = (first & 0x01) == 1
        if len(data) == 1:
            return ""
        return _decode_arithmetic(data[1:], model, has_escapes)


# --- v3 container (mcmp_app_codec.dart) ---------------------------------------

_V3_FLAG_REPLY = 1 << 0
_V3_FLAG_SIGNED = 1 << 1
_V3_FLAG_SENDER_NAME = 1 << 2
_V3_KNOWN_FLAGS = _V3_FLAG_REPLY | _V3_FLAG_SIGNED | _V3_FLAG_SENDER_NAME
_V3_SIGNATURE_SIZE = 64


class DecodedV3Message:
    """Result of decoding an ``mcmp3:`` container."""

    __slots__ = (
        "text",
        "timestamp",
        "sender_name",
        "is_signed",
        "reply_author_name",
        "reply_timestamp",
    )

    def __init__(
        self,
        text: str,
        timestamp: int,
        sender_name: str | None,
        is_signed: bool,
        reply_author_name: str | None,
        reply_timestamp: int | None,
    ) -> None:
        self.text = text
        self.timestamp = timestamp
        self.sender_name = sender_name
        self.is_signed = is_signed
        self.reply_author_name = reply_author_name
        self.reply_timestamp = reply_timestamp


def _write_varuint(out: bytearray, value: int) -> None:
    if value < 0:
        raise MeshCompressorError("varuint cannot be negative")
    while True:
        byte = value & 0x7F
        value >>= 7
        if value != 0:
            byte |= 0x80
        out.append(byte)
        if value == 0:
            break


class _ByteReader:
    __slots__ = ("data", "offset")

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.offset = 0

    def read_byte(self) -> int:
        if self.offset >= len(self.data):
            raise MeshCompressorError("unexpected end of MCMP v3 body")
        value = self.data[self.offset]
        self.offset += 1
        return value

    def read_bytes(self, length: int) -> bytes:
        if length < 0 or self.offset + length > len(self.data):
            raise MeshCompressorError("unexpected end of MCMP v3 body")
        chunk = self.data[self.offset : self.offset + length]
        self.offset += length
        return chunk

    def read_uint32_le(self) -> int:
        b = self.read_bytes(4)
        return b[0] | (b[1] << 8) | (b[2] << 16) | (b[3] << 24)

    def read_varuint(self) -> int:
        result = 0
        shift = 0
        while True:
            byte = self.read_byte()
            result |= (byte & 0x7F) << shift
            if (byte & 0x80) == 0:
                return result
            shift += 7
            if shift > 28:
                raise MeshCompressorError("MCMP v3 varuint too long")

    def read_remaining(self) -> bytes:
        return self.read_bytes(len(self.data) - self.offset)


def is_v3_text_payload(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith(_PREFIX_V3) and len(stripped) > len(_PREFIX_V3)


def encode_v3_body(
    compressor: MeshCompressor,
    text: str,
    timestamp: int,
    sender_name: str | None = None,
    reply_author_name: str | None = None,
    reply_timestamp: int | None = None,
) -> bytes:
    """Build an unsigned v3 container body (signature bit is never set)."""
    if timestamp < 0 or timestamp > 0xFFFFFFFF:
        raise MeshCompressorError("timestamp out of range")
    if (reply_author_name is None) != (reply_timestamp is None):
        raise MeshCompressorError("reply name and timestamp must be provided together")
    if reply_timestamp is not None and (reply_timestamp < 0 or reply_timestamp > 0xFFFFFFFF):
        raise MeshCompressorError("reply timestamp out of range")

    flags = 0
    if reply_author_name is not None:
        flags |= _V3_FLAG_REPLY
    if sender_name is not None:
        flags |= _V3_FLAG_SENDER_NAME

    compressed = compressor.compress_to_bytes(text)
    out = bytearray()
    out.append(flags)
    out += int(timestamp).to_bytes(4, "little")
    if sender_name is not None:
        name_bytes = sender_name.encode("utf-8")
        _write_varuint(out, len(name_bytes))
        out += name_bytes
    if reply_author_name is not None and reply_timestamp is not None:
        reply_bytes = reply_author_name.encode("utf-8")
        _write_varuint(out, len(reply_bytes))
        out += reply_bytes
        out += int(reply_timestamp).to_bytes(4, "little")
    out += compressed
    return bytes(out)


def decode_v3_body(compressor: MeshCompressor, body: bytes) -> DecodedV3Message:
    reader = _ByteReader(body)
    flags = reader.read_byte()
    if (flags & ~_V3_KNOWN_FLAGS) != 0:
        raise MeshCompressorError("unsupported MCMP v3 flags")
    timestamp = reader.read_uint32_le()

    sender_name: str | None = None
    if flags & _V3_FLAG_SENDER_NAME:
        sender_name = reader.read_bytes(reader.read_varuint()).decode("utf-8")

    is_signed = bool(flags & _V3_FLAG_SIGNED)
    if is_signed:
        # Signature is read past and NOT verified (Ed25519 verification needs
        # the radio firmware; deferred). The message text is still recovered.
        reader.read_bytes(_V3_SIGNATURE_SIZE)

    reply_author_name: str | None = None
    reply_timestamp: int | None = None
    if flags & _V3_FLAG_REPLY:
        reply_author_name = reader.read_bytes(reader.read_varuint()).decode("utf-8")
        reply_timestamp = reader.read_uint32_le()

    compressed = reader.read_remaining()
    text = compressor.decompress_bytes(compressed)
    return DecodedV3Message(
        text=text,
        timestamp=timestamp,
        sender_name=sender_name,
        is_signed=is_signed,
        reply_author_name=reply_author_name,
        reply_timestamp=reply_timestamp,
    )


def encode_v3_text(
    compressor: MeshCompressor,
    text: str,
    timestamp: int,
    sender_name: str | None = None,
    reply_author_name: str | None = None,
    reply_timestamp: int | None = None,
) -> str:
    """Encode ``text`` into the ``mcmp3:`` text transport (always container)."""
    if not text or is_v3_text_payload(text) or compressor.has_prefix(text):
        return text
    try:
        body = encode_v3_body(
            compressor,
            text=text,
            timestamp=timestamp,
            sender_name=sender_name,
            reply_author_name=reply_author_name,
            reply_timestamp=reply_timestamp,
        )
        return f"{_PREFIX_V3}{_b91_encode_v3(body)}"
    except Exception:
        return text


def try_decode_v3_text(compressor: MeshCompressor, text: str) -> DecodedV3Message | None:
    if not is_v3_text_payload(text):
        return None
    try:
        stripped = text.lstrip()
        body = _b91_decode_v3(stripped[len(_PREFIX_V3) :])
        return decode_v3_body(compressor, body)
    except Exception:
        return None


# --- module-level singleton + dispatch ---------------------------------------

_singleton: MeshCompressor | None = None
_singleton_lock = threading.Lock()
_model_load_failed = False


def get_compressor() -> MeshCompressor:
    """Return the process-wide compressor, loading the bundled model on first use."""
    global _singleton
    if _singleton is not None and _singleton.is_ready:
        return _singleton
    with _singleton_lock:
        if _singleton is None or not _singleton.is_ready:
            compressor = MeshCompressor()
            compressor.load_from_path()
            _singleton = compressor
    return _singleton


def _log_model_load_failure() -> None:
    global _model_load_failed
    if not _model_load_failed:
        _model_load_failed = True
        logger.warning("MCMP model unavailable; outbound compression disabled", exc_info=True)


def encode_outbound(text: str) -> str:
    """Compress ``text`` for sending, as the v2 text transport (``mcmp2:``).

    Uses the "only if smaller" gate: returns the original text unchanged when
    compression would not shrink it (so short/incompressible messages stay
    readable by any client). Never raises — on model-load failure the original
    text is returned so sending still works, just uncompressed.

    We send v2 rather than v3: without a firmware signature v3 only adds
    container overhead, and v2's plaintext fallback keeps small messages
    universally readable. (v3 is still decoded on the way in.)
    """
    if not text:
        return text
    try:
        return get_compressor().encode_if_smaller(text)
    except Exception:
        _log_model_load_failure()
        return text


def outbound_wire_bytes(text: str) -> int:
    """UTF-8 byte length ``text`` would occupy on the wire once compressed.

    Drives the live compose counter: the frontend shows this against the packet
    budget so the effective character capacity grows as compressible text is
    typed. Falls back to the raw byte length if the model is unavailable.
    """
    return len(encode_outbound(text).encode("utf-8"))


class DecodedIncoming:
    """Result of :func:`try_decode_incoming`."""

    __slots__ = ("text", "version", "v3")

    def __init__(self, text: str, version: str, v3: DecodedV3Message | None) -> None:
        self.text = text
        self.version = version  # "v2" or "v3"
        self.v3 = v3


def try_decode_incoming(text: str) -> DecodedIncoming | None:
    """Decode an incoming message body if it is MCMP (v3 first, then v2).

    Returns ``None`` for plain text (or anything not recognizably MCMP), so the
    caller can store the body unchanged. Never raises: if the model cannot be
    loaded, or the payload is malformed, the message is left undecoded (and
    displays as its raw ``mcmp2:``/``mcmp3:`` string) rather than breaking
    ingest.
    """
    if not text:
        return None
    stripped = text.lstrip()
    if not (stripped.startswith(_PREFIX_V3) or stripped.startswith(_PREFIX_V2)):
        return None

    try:
        compressor = get_compressor()
    except Exception:
        _log_model_load_failure()
        return None

    v3 = try_decode_v3_text(compressor, text)
    if v3 is not None:
        return DecodedIncoming(text=v3.text, version="v3", v3=v3)

    decoded = compressor.try_decode_prefixed(text)
    if decoded is not None:
        return DecodedIncoming(text=decoded, version="v2", v3=None)

    return None
