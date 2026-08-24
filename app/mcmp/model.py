from __future__ import annotations

import math
from typing import List, Dict, Set, Tuple, Optional


class CdfEntry:
    __slots__ = ('symbol', 'low', 'high')

    def __init__(self, symbol: str, low: int, high: int):
        self.symbol = symbol
        self.low = low
        self.high = high


class UnicodeBlock:
    __slots__ = ('id', 'start', 'end')

    def __init__(self, block_id: int, start: int, end: int):
        self.id = block_id
        self.start = start
        self.end = end

    @property
    def size(self) -> int:
        return self.end - self.start + 1


class MeshCompressionModel:
    CDF_SCALE = 1 << 20
    PRECISION = 32
    FULL = 1 << PRECISION
    HALF = 1 << (PRECISION - 1)
    QUARTER = 1 << (PRECISION - 2)
    THREE_QUARTER = 3 * QUARTER
    MASK = FULL - 1

    SCRIPT_BOOST = 8
    ESC_PROB = 500
    CDF_CACHE_MAX = 50000
    DECODE_HARD_LIMIT = 4096

    BOS = '\x02'
    EOF = '\x03'
    ESC = '\x04'

    UNICODE_BLOCKS: List[UnicodeBlock] = [
        UnicodeBlock(0, 0x0400, 0x04FF),
        UnicodeBlock(1, 0x0100, 0x024F),
        UnicodeBlock(2, 0x2000, 0x206F),
        UnicodeBlock(3, 0x2190, 0x21FF),
        UnicodeBlock(4, 0x2600, 0x27BF),
        UnicodeBlock(5, 0x1F300, 0x1F5FF),
        UnicodeBlock(6, 0x1F600, 0x1F64F),
        UnicodeBlock(7, 0x1F900, 0x1F9FF),
        UnicodeBlock(8, 0xFE00, 0xFE0F),
        UnicodeBlock(9, 0x1FA70, 0x1FAFF),
    ]
    NUM_BLOCKS = len(UNICODE_BLOCKS)
    FALLBACK_BLOCK_ID = NUM_BLOCKS
    TOTAL_BLOCK_IDS = NUM_BLOCKS + 1

    def __init__(self, order: int, vocab: List[str], counts: List[Dict[str, Dict[str, int]]]):
        self.order = order
        self.vocab: List[str] = vocab
        self.vocab_set: Set[str] = set(vocab)
        self.vocab_index: Dict[str, int] = {ch: i for i, ch in enumerate(vocab)}
        self.counts: List[Dict[str, Dict[str, int]]] = counts
        self.totals: List[Dict[str, int]] = [
            {
                ctx: sum(entry.values())
                for ctx, entry in counts[n].items()
            }
            for n in range(order + 1)
        ]
        self.char_scripts: Dict[str, str] = {}
        self._cdf_cache: Dict[str, List[CdfEntry]] = {}

        for ch in vocab:
            self.char_scripts[ch] = self._char_script(ch)

    @classmethod
    def from_json(cls, json_dict: dict) -> 'MeshCompressionModel':
        order = int(json_dict['o'])
        vocab = list(json_dict['v'])
        for sym in [cls.EOF, cls.ESC]:
            if sym not in vocab:
                vocab.append(sym)

        # Dart List<String>.sort() compares by UTF-16 code units.
        # Python str.sort compares by Unicode code points.
        # For emoji and other supplementary characters this gives a different order,
        # which breaks arithmetic-coding compatibility.
        # Sorting by utf-16-be bytes reproduces Dart's ordering exactly.
        vocab.sort(key=lambda s: s.encode('utf-16-be'))

        raw_counts = json_dict['c']
        counts: List[Dict[str, Dict[str, int]]] = []
        for n in range(order + 1):
            contexts = raw_counts[n]
            c_map: Dict[str, Dict[str, int]] = {}
            for ctx, counts_map in contexts.items():
                c_map[ctx] = {k: int(v) for k, v in counts_map.items()}
            counts.append(c_map)

        return cls(order, vocab, counts)

    def _char_script(self, ch: str) -> str:
        cp = ord(ch)
        if cp < 0x0041:
            return 'Common'
        if cp <= 0x024F or (0x1E00 <= cp <= 0x1EFF):
            return 'Latin'
        if 0x0400 <= cp <= 0x052F:
            return 'Cyrillic'
        if cp > 0xFFFF:
            return 'Common'
        return 'Other'

    def _append_context(self, context: str, ch: str, order: int) -> str:
        combined = context + ch
        if len(combined) <= order:
            return combined
        return combined[-order:]

    def get_cdf(self, context: str, has_escapes: bool) -> List[CdfEntry]:
        cache_key = f"{1 if has_escapes else 0}|{context}"
        cached = self._cdf_cache.get(cache_key)
        if cached is not None:
            return cached

        cdf = self._compute_cdf(context, has_escapes)
        if len(self._cdf_cache) < self.CDF_CACHE_MAX:
            self._cdf_cache[cache_key] = cdf
        return cdf

    def _compute_cdf(self, context: str, has_escapes: bool) -> List[CdfEntry]:
        active: List[Tuple[int, str, int, float]] = []
        total_weight = 0.0
        max_match_order = -1

        for n in range(self.order, -1, -1):
            if n > 0:
                ctx = context[-n:] if len(context) >= n else context
            else:
                ctx = ''
            total = self.totals[n].get(ctx, 0)
            if total <= 0:
                continue
            confidence = total / (total + 1.5)
            weight = ((n + 1) ** 3) * math.log(total + 1) * confidence
            active.append((n, ctx, total, weight))
            total_weight += weight
            if n > max_match_order:
                max_match_order = n

        effective_script_boost = self.SCRIPT_BOOST * 4 if max_match_order <= 2 else self.SCRIPT_BOOST

        context_script = None
        for i in range(len(context) - 1, -1, -1):
            ch = context[i]
            if ch == self.BOS:
                continue
            context_script = self.char_scripts.get(ch, self._char_script(ch))
            if context_script != 'Common':
                break

        compat_scripts: Optional[Set[str]] = None
        if context_script is not None and context_script != 'Common':
            compat_scripts = {context_script, 'Common'}

        freqs = [0] * len(self.vocab)
        epsilon_total = 0
        for i, ch in enumerate(self.vocab):
            ch_script = self.char_scripts.get(ch, 'Other')
            if ch == self.ESC:
                epsilon = self.ESC_PROB if has_escapes else 0
            elif compat_scripts is not None and ch_script in compat_scripts:
                epsilon = effective_script_boost
            elif ch_script == 'Common':
                epsilon = max(1, effective_script_boost // 3)
            else:
                epsilon = 1
            freqs[i] = epsilon
            epsilon_total += epsilon

        if epsilon_total > self.CDF_SCALE // 2:
            scale_factor = (self.CDF_SCALE // 2) / epsilon_total
            epsilon_total = 0
            for i in range(len(freqs)):
                freqs[i] = max(1, int(freqs[i] * scale_factor))
                epsilon_total += freqs[i]

        if total_weight > 0:
            scale = self.CDF_SCALE - epsilon_total
            for n, ctx, total, weight in active:
                counts_for_context = self.counts[n].get(ctx)
                if not counts_for_context:
                    continue
                factor = (weight / total_weight) * scale / total
                for symbol, count in counts_for_context.items():
                    idx = self.vocab_index.get(symbol)
                    if idx is None:
                        continue
                    freqs[idx] += int(count * factor)

        total_freq = sum(freqs)
        if total_freq != self.CDF_SCALE:
            diff = self.CDF_SCALE - total_freq
            if diff > 0:
                max_idx = 0
                for i in range(1, len(freqs)):
                    if freqs[i] > freqs[max_idx]:
                        max_idx = i
                freqs[max_idx] += diff
            else:
                indices = sorted(range(len(freqs)), key=lambda i: freqs[i], reverse=True)
                remaining = -diff
                for idx in indices:
                    if remaining <= 0:
                        break
                    can_remove = freqs[idx] - 1
                    remove = min(can_remove, remaining)
                    freqs[idx] -= remove
                    remaining -= remove

        cdf = []
        cumulative = 0
        for i, freq in enumerate(freqs):
            cdf.append(CdfEntry(self.vocab[i], cumulative, cumulative + freq))
            cumulative += freq
        return cdf