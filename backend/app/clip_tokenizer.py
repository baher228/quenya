import gzip
import html
import re
from functools import lru_cache
from pathlib import Path

import ftfy
import numpy as np
import regex

_VOCAB = Path(__file__).parent / "data" / "bpe_simple_vocab_16e6.txt.gz"
_CONTEXT_LENGTH = 77


@lru_cache
def _bytes_to_unicode() -> dict[int, str]:
    byte_values = (
        list(range(ord("!"), ord("~") + 1))
        + list(range(ord("¡"), ord("¬") + 1))
        + list(range(ord("®"), ord("ÿ") + 1))
    )
    characters = byte_values[:]
    offset = 0
    for value in range(256):
        if value not in byte_values:
            byte_values.append(value)
            characters.append(256 + offset)
            offset += 1
    return dict(zip(byte_values, (chr(value) for value in characters), strict=True))


def _pairs(word: tuple[str, ...]) -> set[tuple[str, str]]:
    return set(zip(word, word[1:], strict=False))


class SimpleTokenizer:
    def __init__(self, vocab_path: Path | str = _VOCAB):
        self.byte_encoder = _bytes_to_unicode()
        self.byte_decoder = {value: key for key, value in self.byte_encoder.items()}
        with gzip.open(vocab_path) as vocab_file:
            merges = vocab_file.read().decode("utf-8").split("\n")
        merges = merges[1 : 49152 - 256 - 2 + 1]
        merges = [tuple(merge.split()) for merge in merges]
        vocab = list(self.byte_encoder.values())
        vocab += [value + "</w>" for value in self.byte_encoder.values()]
        vocab += ["".join(merge) for merge in merges]
        special_tokens = ["<start_of_text>", "<end_of_text>"]
        vocab += special_tokens
        self.encoder = dict(zip(vocab, range(len(vocab)), strict=True))
        self.decoder = {value: key for key, value in self.encoder.items()}
        self.bpe_ranks = dict(zip(merges, range(len(merges)), strict=True))
        self.cache = {token: token for token in special_tokens}
        self.pattern = regex.compile(
            r"<start_of_text>|<end_of_text>|'s|'t|'re|'ve|'m|'ll|'d|"
            r"[\p{L}]+|[\p{N}]|[^\s\p{L}\p{N}]+",
            regex.IGNORECASE,
        )
        self.sot_token_id = self.encoder["<start_of_text>"]
        self.eot_token_id = self.encoder["<end_of_text>"]

    @staticmethod
    def _clean(text: str) -> str:
        text = ftfy.fix_text(text)
        text = html.unescape(html.unescape(text)).strip()
        return re.sub(r"\s+", " ", text).strip().lower()

    def _bpe(self, token: str) -> str:
        if token in self.cache:
            return self.cache[token]
        word = tuple(token[:-1]) + (token[-1] + "</w>",)
        pairs = _pairs(word)
        if not pairs:
            return token + "</w>"
        while pairs:
            bigram = min(pairs, key=lambda pair: self.bpe_ranks.get(pair, float("inf")))
            if bigram not in self.bpe_ranks:
                break
            first, second = bigram
            merged = []
            index = 0
            while index < len(word):
                try:
                    next_index = word.index(first, index)
                    merged.extend(word[index:next_index])
                    index = next_index
                except ValueError:
                    merged.extend(word[index:])
                    break
                if (
                    word[index] == first
                    and index < len(word) - 1
                    and word[index + 1] == second
                ):
                    merged.append(first + second)
                    index += 2
                else:
                    merged.append(word[index])
                    index += 1
            word = tuple(merged)
            if len(word) == 1:
                break
            pairs = _pairs(word)
        result = " ".join(word)
        self.cache[token] = result
        return result

    def encode(self, text: str) -> list[int]:
        tokens = []
        for token in regex.findall(self.pattern, self._clean(text)):
            token = "".join(self.byte_encoder[value] for value in token.encode("utf-8"))
            tokens.extend(self.encoder[piece] for piece in self._bpe(token).split(" "))
        return tokens

    def __call__(self, texts: str | list[str], context_length: int = _CONTEXT_LENGTH):
        if isinstance(texts, str):
            texts = [texts]
        result = np.zeros((len(texts), context_length), dtype=np.int64)
        for index, text in enumerate(texts):
            tokens = [self.sot_token_id, *self.encode(text), self.eot_token_id]
            if len(tokens) > context_length:
                tokens = tokens[:context_length]
                tokens[-1] = self.eot_token_id
            result[index, : len(tokens)] = tokens
        return result
