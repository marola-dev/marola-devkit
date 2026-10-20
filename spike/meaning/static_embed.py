"""MIP-0085 §5.7 spike (throwaway): a model2vec query vector with the stdlib only.

The tokenizer is rebuilt from tokenizer.json (BertNormalizer, BertPreTokenizer, WordPiece; anything
else is refused, §11), the rows are read from model.safetensors with `struct` (F32/F16 only; never a
pickle), and the vector is model2vec 0.9.0's: unknown tokens dropped, the mean, then L2-normalised.
"""

from __future__ import annotations

import json
import statistics
import string
import struct
import sys
import unicodedata
from array import array
from pathlib import Path

ASCII_PUNCT = frozenset(string.punctuation)


class Unsupported(Exception):
    pass


def _chinese(cp: int) -> bool:
    return (
        0x4E00 <= cp <= 0x9FFF
        or 0x3400 <= cp <= 0x4DBF
        or 0x20000 <= cp <= 0x2A6DF
        or 0x2A700 <= cp <= 0x2B73F
        or 0x2B740 <= cp <= 0x2B81F
        or 0x2B820 <= cp <= 0x2CEAF
        or 0xF900 <= cp <= 0xFAFF
        or 0x2F800 <= cp <= 0x2FA1F
    )


def _space(c: str) -> bool:
    # Rust's char::is_whitespace is Unicode White_Space; Python's isspace adds \x1c-\x1f, which
    # the control filter has already removed by the time this runs.
    return c in "\t\n\r" or c.isspace()


def _control(c: str) -> bool:
    return c not in "\t\n\r" and unicodedata.category(c)[0] == "C"


class Tokenizer:
    def __init__(self, spec: dict):
        norm, pre, model = (
            spec.get("normalizer") or {},
            spec.get("pre_tokenizer") or {},
            spec["model"],
        )
        if norm.get("type") != "BertNormalizer":
            raise Unsupported(f"normalizer {norm.get('type')!r}")
        if pre.get("type") != "BertPreTokenizer":
            raise Unsupported(f"pre_tokenizer {pre.get('type')!r}")
        if model.get("type") != "WordPiece":
            raise Unsupported(f"model {model.get('type')!r}")
        self.clean = norm.get("clean_text", True)
        self.cjk = norm.get("handle_chinese_chars", True)
        self.lower = norm.get("lowercase", True)
        sa = norm.get("strip_accents")
        self.strip = self.lower if sa is None else sa
        self.vocab: dict[str, int] = model["vocab"]
        self.unk = model["unk_token"]
        self.unk_id = self.vocab.get(self.unk)
        self.prefix = model.get("continuing_subword_prefix", "##")
        self.max_chars = model.get("max_input_chars_per_word", 100)
        self.longest = max(len(t) for t in self.vocab)

    def normalize(self, text: str) -> str:
        if self.clean:
            text = "".join(
                " " if _space(c) else c for c in text if not (c == "\0" or c == "�" or _control(c))
            )
        if self.cjk:
            text = "".join(f" {c} " if _chinese(ord(c)) else c for c in text)
        if self.strip:
            text = "".join(
                c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
            )
        if self.lower:
            text = text.lower()
        return text

    def pre_tokenize(self, text: str) -> list[str]:
        words, cur = [], []
        for c in text:
            if _space(c):
                if cur:
                    words.append("".join(cur))
                    cur = []
            elif c in ASCII_PUNCT or unicodedata.category(c).startswith("P"):
                if cur:
                    words.append("".join(cur))
                    cur = []
                words.append(c)
            else:
                cur.append(c)
        if cur:
            words.append("".join(cur))
        return words

    def wordpiece(self, word: str) -> list[int]:
        if len(word) > self.max_chars:
            return [self.unk_id]
        out, start = [], 0
        while start < len(word):
            end = min(len(word), start + self.longest)
            hit = None
            while start < end:
                piece = word[start:end] if start == 0 else self.prefix + word[start:end]
                if piece in self.vocab:
                    hit = self.vocab[piece]
                    break
                end -= 1
            if hit is None:
                return [self.unk_id]
            out.append(hit)
            start = end
        return out

    def ids(self, text: str) -> list[int]:
        return [i for w in self.pre_tokenize(self.normalize(text)) for i in self.wordpiece(w)]


class Safetensors:
    """Rows of one 2-D tensor, read on demand by offset; the file is never executed or unpickled."""

    def __init__(self, path: Path, name: str = "embeddings"):
        self.f = open(path, "rb")  # noqa: SIM115
        (n,) = struct.unpack("<Q", self.f.read(8))
        if n > 10_000_000:
            raise Unsupported("safetensors header too large")
        header = json.loads(self.f.read(n))
        t = header[name]
        self.dtype, (self.rows, self.dim) = t["dtype"], t["shape"]
        width = {"F32": 4, "F16": 2}.get(self.dtype)
        if width is None:
            raise Unsupported(f"dtype {self.dtype}")
        self.width, self.fmt = width, f"<{self.dim}{'f' if width == 4 else 'e'}"
        start, end = t["data_offsets"]
        if end - start != self.rows * self.dim * width:
            raise Unsupported("tensor size does not match its shape")
        self.base = 8 + n + start

    def row(self, i: int) -> tuple[float, ...]:
        self.f.seek(self.base + i * self.dim * self.width)
        return struct.unpack(self.fmt, self.f.read(self.dim * self.width))


class StaticModel:
    def __init__(self, model_dir: Path):
        spec = json.loads((model_dir / "tokenizer.json").read_text(encoding="utf-8"))
        self.tok = Tokenizer(spec)
        self.emb = Safetensors(model_dir / "model.safetensors")
        if self.emb.rows != len(self.tok.vocab):
            raise Unsupported("vocab and embedding rows differ (a token mapping is not supported)")
        self.dim = self.emb.dim
        # model2vec truncates text at max_length * the vocab's median token length, in characters.
        by_id = sorted(self.tok.vocab, key=self.tok.vocab.__getitem__)
        self.median = int(statistics.median(len(t) for t in by_id))

    def encode(self, text: str, max_length: int = 512) -> list[float]:
        ids = self.tok.ids(text[: max_length * self.median])
        ids = [i for i in ids if i != self.tok.unk_id][:max_length]
        if not ids:
            return [0.0] * self.dim
        acc = [0.0] * self.dim
        for i in ids:
            for k, v in enumerate(self.emb.row(i)):
                acc[k] += v
        mean = [v / len(ids) for v in acc]
        norm = sum(v * v for v in mean) ** 0.5 + 1e-32
        return [v / norm for v in mean]


def load_vectors(path: Path, dim: int) -> list[array]:
    data = array("f")
    data.frombytes(path.read_bytes())
    if sys.byteorder == "big":
        data.byteswap()
    return [data[i : i + dim] for i in range(0, len(data), dim)]


def cosine_rank(q: list[float], vecs: list[array]) -> list[tuple[float, int]]:
    scored = [(sum(a * b for a, b in zip(q, v, strict=True)), i) for i, v in enumerate(vecs)]
    scored.sort(key=lambda s: (-s[0], s[1]))
    return scored
