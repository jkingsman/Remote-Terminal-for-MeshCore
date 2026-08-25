# app/mcmp/compressor.py
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional, Tuple

from .model import CdfEntry, MeshCompressionModel

logger = logging.getLogger(__name__)


class _ArithmeticEncoder:
    def __init__(self, precision: int = 32):
        self.low = 0
        self.high = (1 << precision) - 1
        self.pending = 0
        self.bits: List[int] = []
        self.precision = precision
        self.mask = (1 << precision) - 1
        self.half = 1 << (precision - 1)
        self.quarter = 1 << (precision - 2)
        self.three_quarter = 3 * self.quarter

    def encode_symbol(self, low_count: int, high_count: int, total: int) -> None:
        range_size = self.high - self.low + 1
        total_big = total
        self.high = self.low + (range_size * high_count) // total_big - 1
        self.low = self.low + (range_size * low_count) // total_big

        while True:
            if self.high < self.half:
                self._emit_bit(0)
            elif self.low >= self.half:
                self._emit_bit(1)
                self.low -= self.half
                self.high -= self.half
            elif self.low >= self.quarter and self.high < self.three_quarter:
                self.pending += 1
                self.low -= self.quarter
                self.high -= self.quarter
            else:
                break
            self.low = (self.low << 1) & self.mask
            self.high = ((self.high << 1) | 1) & self.mask

    def finish_bits(self) -> List[int]:
        self.pending += 1
        if self.low < self.quarter:
            self._emit_bit(0)
        else:
            self._emit_bit(1)
        return list(self.bits)

    def _emit_bit(self, bit: int) -> None:
        self.bits.append(bit)
        opposite = 1 - bit
        for _ in range(self.pending):
            self.bits.append(opposite)
        self.pending = 0


class _ArithmeticDecoder:
    def __init__(self, data: bytes, precision: int = 32):
        self.data = data
        self.total_bits = len(data) * 8
        self.precision = precision
        self.mask = (1 << precision) - 1
        self.half = 1 << (precision - 1)
        self.quarter = 1 << (precision - 2)
        self.three_quarter = 3 * self.quarter
        self.low = 0
        self.high = self.mask
        self.value = 0
        self.bit_pos = 0
        for _ in range(precision):
            self.value = (self.value << 1) | self._read_bit()

    def decode_symbol_index(self, cdf: List[CdfEntry], total: int = 1 << 20) -> int:
        range_size = self.high - self.low + 1
        scaled = (((self.value - self.low + 1) * total) - 1) // range_size
        scaled_int = int(scaled)

        left, right = 0, len(cdf) - 1
        while left < right:
            mid = (left + right) >> 1
            if cdf[mid].high <= scaled_int:
                left = mid + 1
            else:
                right = mid
        entry = cdf[left]

        self.high = self.low + (range_size * entry.high) // total - 1
        self.low = self.low + (range_size * entry.low) // total

        while True:
            if self.high < self.half:
                pass
            elif self.low >= self.half:
                self.low -= self.half
                self.high -= self.half
                self.value -= self.half
            elif self.low >= self.quarter and self.high < self.three_quarter:
                self.low -= self.quarter
                self.high -= self.quarter
                self.value -= self.quarter
            else:
                break
            self.low = (self.low << 1) & self.mask
            self.high = ((self.high << 1) | 1) & self.mask
            self.value = ((self.value << 1) | self._read_bit()) & self.mask
        return left

    def _read_bit(self) -> int:
        if self.bit_pos >= self.total_bits:
            return 0
        byte_index = self.bit_pos >> 3
        bit_index = 7 - (self.bit_pos & 7)
        self.bit_pos += 1
        return (self.data[byte_index] >> bit_index) & 1


class MeshCompressor:
    _instance: Optional['MeshCompressor'] = None

    def __init__(self):
        self._model: Optional[MeshCompressionModel] = None
        self._ready: bool = False

    @classmethod
    def get_instance(cls) -> 'MeshCompressor':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def is_ready(self) -> bool:
        return self._ready

    async def initialize(self, model_path: Optional[Path] = None) -> None:
        """Load model from JSON file. Logs detailed error if loading fails."""
        if self._ready:
            return
        if model_path is None:
            model_path = Path(__file__).parent / 'models' / 'model-en-ru.json'

        logger.info("Loading MCMP model from %s", model_path)
        try:
            text = model_path.read_text(encoding='utf-8')
            if not text.strip():
                raise ValueError("Model file is empty")
            data = json.loads(text)
            if not isinstance(data, dict) or 'o' not in data or 'v' not in data or 'c' not in data:
                raise ValueError("Model JSON missing required keys: o, v, c")
            self._model = MeshCompressionModel.from_json(data)
            self._ready = True
            logger.info(
                "MCMP model loaded successfully: order=%d, vocab=%d symbols",
                self._model.order,
                len(self._model.vocab),
            )
        except FileNotFoundError:
            logger.error("MCMP model file not found at %s", model_path)
            self._ready = False
            self._model = None
        except Exception:
            logger.exception("Failed to load MCMP model from %s", model_path)
            self._ready = False
            self._model = None

    def _require_model(self) -> MeshCompressionModel:
        if not self._ready or self._model is None:
            raise RuntimeError("MeshCompressor model is not initialized")
        return self._model

    def compress_to_bytes(self, text: str) -> bytes:
        """Compress text to bytes (flags byte + arithmetic-coded bytes)."""
        model = self._require_model()
        if text == '':
            return b''
        utf8_bytes = text.encode('utf-8')
        ac_result = self._compress(text, model)
        if len(ac_result) > len(utf8_bytes) and utf8_bytes[0] >= 0x02:
            return utf8_bytes
        return ac_result

    def decompress_bytes(self, data: bytes) -> str:
        """Decompress bytes produced by compress_to_bytes."""
        model = self._require_model()
        return self._decompress(data, model)

    # --- Core compression ---
    def _compress(self, text: str, model: MeshCompressionModel) -> bytes:
        if not text:
            return b''
        flags, bits = self._compress_arithmetic_bits(text, model)
        ac_bytes = self._bits_to_bytes(bits)
        return bytes([flags]) + ac_bytes

    def _compress_arithmetic_bits(self, text: str, model: MeshCompressionModel) -> Tuple[int, List[int]]:
        has_extras = any(ch not in model.vocab_set for ch in text)
        flags = 1 if has_extras else 0
        encoder = _ArithmeticEncoder()
        context = model.BOS * model.order

        for ch in text:
            cdf = model.get_cdf(context, has_extras)
            if ch in model.vocab_set:
                entry = next(e for e in cdf if e.symbol == ch)
                encoder.encode_symbol(entry.low, entry.high, model.CDF_SCALE)
            else:
                esc_entry = next(e for e in cdf if e.symbol == model.ESC)
                encoder.encode_symbol(esc_entry.low, esc_entry.high, model.CDF_SCALE)
                self._encode_codepoint(encoder, ord(ch))
            context = model._append_context(context, ch, model.order)

        eof_entry = next(e for e in model.get_cdf(context, has_extras) if e.symbol == model.EOF)
        encoder.encode_symbol(eof_entry.low, eof_entry.high, model.CDF_SCALE)

        return (flags & 0x01), encoder.finish_bits()

    def _decompress(self, data: bytes, model: MeshCompressionModel) -> str:
        if not data:
            return ''
        first = data[0]
        if first > 0x01:
            return data.decode('utf-8')
        has_escapes = (first & 0x01) == 1
        ac_data = data[1:]
        decoder = _ArithmeticDecoder(ac_data)
        context = model.BOS * model.order
        result = []

        for _ in range(model.DECODE_HARD_LIMIT):
            cdf = model.get_cdf(context, has_escapes)
            idx = decoder.decode_symbol_index(cdf, model.CDF_SCALE)
            ch = cdf[idx].symbol
            if ch == model.EOF:
                break
            if ch == model.ESC and has_escapes:
                cp = self._decode_codepoint(decoder)
                ch = chr(cp)
            result.append(ch)
            context = model._append_context(context, ch, model.order)
        return ''.join(result)

    # --- Unicode codepoint encoding ---
    def _encode_codepoint(self, encoder: _ArithmeticEncoder, codepoint: int) -> None:
        for block in MeshCompressionModel.UNICODE_BLOCKS:
            if block.start <= codepoint <= block.end:
                encoder.encode_symbol(block.id, block.id + 1, MeshCompressionModel.TOTAL_BLOCK_IDS)
                offset = codepoint - block.start
                encoder.encode_symbol(offset, offset + 1, block.size)
                return

        encoder.encode_symbol(
            MeshCompressionModel.FALLBACK_BLOCK_ID,
            MeshCompressionModel.FALLBACK_BLOCK_ID + 1,
            MeshCompressionModel.TOTAL_BLOCK_IDS,
        )
        encoder.encode_symbol(codepoint & 0x7F, (codepoint & 0x7F) + 1, 128)
        encoder.encode_symbol((codepoint >> 7) & 0x7F, ((codepoint >> 7) & 0x7F) + 1, 128)
        encoder.encode_symbol((codepoint >> 14) & 0x7F, ((codepoint >> 14) & 0x7F) + 1, 128)

    def _decode_codepoint(self, decoder: _ArithmeticDecoder) -> int:
        total_blocks = MeshCompressionModel.TOTAL_BLOCK_IDS
        block_cdf = [CdfEntry('', i, i + 1) for i in range(total_blocks)]
        block_id = decoder.decode_symbol_index(block_cdf, total_blocks)
        if block_id < MeshCompressionModel.NUM_BLOCKS:
            block = MeshCompressionModel.UNICODE_BLOCKS[block_id]
            offset_cdf = [CdfEntry('', i, i + 1) for i in range(block.size)]
            offset = decoder.decode_symbol_index(offset_cdf, block.size)
            return block.start + offset

        cp_cdf = [CdfEntry('', i, i + 1) for i in range(128)]
        b0 = decoder.decode_symbol_index(cp_cdf, 128)
        b1 = decoder.decode_symbol_index(cp_cdf, 128)
        b2 = decoder.decode_symbol_index(cp_cdf, 128)
        return b0 | (b1 << 7) | (b2 << 14)

    # --- Helpers ---
    def _bits_to_bytes(self, bits: List[int]) -> bytes:
        if not bits:
            return b''
        out = bytearray((len(bits) + 7) >> 3)
        for i, bit in enumerate(bits):
            if bit:
                out[i >> 3] |= 1 << (7 - (i & 7))
        return bytes(out)


# Singleton instance accessible as MeshCompressor.instance
MeshCompressor.instance = MeshCompressor.get_instance()