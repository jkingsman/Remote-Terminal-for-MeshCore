_ALPHABET = (
    'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
    '!#$%&()*+,./:;<=>?@[]^_`{|}~"'
)
_DECODE = {c: i for i, c in enumerate(_ALPHABET)}


def encode(data: bytes) -> str:
    result = []
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
            result.append(_ALPHABET[value % 91])
            result.append(_ALPHABET[value // 91])

    if n:
        result.append(_ALPHABET[b % 91])
        if n > 7 or b > 90:
            result.append(_ALPHABET[b // 91])

    return ''.join(result)


def decode(text: str) -> bytes:
    out = []
    b = 0
    n = 0
    v = -1

    for ch in text:
        try:
            decoded = _DECODE[ch]
        except KeyError:
            raise ValueError(f"Invalid Base91 character: {ch}") from None

        if v < 0:
            v = decoded
        else:
            v += decoded * 91
            b |= v << n
            n += 13 if (v & 8191) > 88 else 14
            while n > 7:
                out.append(b & 0xff)
                b >>= 8
                n -= 8
            v = -1

    if v >= 0:
        out.append((b | (v << n)) & 0xff)

    return bytes(out)