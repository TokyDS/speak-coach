"""Пословное сравнение: что поменял исправитель и насколько точно повторена фраза.

Сравниваем нормализованные слова (регистр, пунктуация, сокращения вроде I'm → i am),
а показываем исходное написание — так подсветка не цепляется к запятым и заглавным буквам.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

_WORD = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z]+)*")
_CONTRACTIONS = {
    "i'm": "i am", "you're": "you are", "we're": "we are", "they're": "they are",
    "it's": "it is", "that's": "that is", "there's": "there is", "he's": "he is", "she's": "she is",
    "i've": "i have", "you've": "you have", "we've": "we have", "they've": "they have",
    "i'll": "i will", "you'll": "you will", "we'll": "we will", "they'll": "they will",
    "i'd": "i would", "don't": "do not", "doesn't": "does not", "didn't": "did not",
    "can't": "can not", "cannot": "can not", "won't": "will not", "isn't": "is not",
    "aren't": "are not", "wasn't": "was not", "weren't": "were not", "haven't": "have not",
    "hasn't": "has not", "wouldn't": "would not", "couldn't": "could not", "shouldn't": "should not",
    "let's": "let us", "gonna": "going to", "wanna": "want to",
}


@dataclass(frozen=True)
class Token:
    text: str   # как написано
    norm: str   # одно нормализованное слово


def tokenize(text: str) -> list[Token]:
    tokens: list[Token] = []
    for match in _WORD.finditer(text):
        raw = match.group(0)
        expanded = _CONTRACTIONS.get(raw.lower().replace("’", "'"), raw.lower().replace("’", "'"))
        parts = expanded.split()
        # «I'm» показываем одним словом, но сравниваем как «i am»: первая часть несёт текст
        tokens.extend(Token(raw if i == 0 else "", part) for i, part in enumerate(parts))
    return tokens


def diff_words(before: str, after: str) -> list[dict]:
    """Список {text, op}: op = equal | delete (было, убрали) | insert (добавили)."""
    a, b = tokenize(before), tokenize(after)
    ops: list[dict] = []
    matcher = SequenceMatcher(a=[t.norm for t in a], b=[t.norm for t in b], autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            ops.extend({"text": t.text, "op": "equal"} for t in b[j1:j2])
            continue
        if tag in ("delete", "replace"):
            ops.extend({"text": t.text, "op": "delete"} for t in a[i1:i2])
        if tag in ("insert", "replace"):
            ops.extend({"text": t.text, "op": "insert"} for t in b[j1:j2])
    return [op for op in ops if op["text"]]


def repeat_score(target: str, heard: str) -> dict:
    """Насколько близко сказанное к образцу: доля слов образца, которые прозвучали по порядку."""
    want, got = tokenize(target), tokenize(heard)
    if not want:
        return {"score": 0, "words": []}
    matcher = SequenceMatcher(a=[t.norm for t in want], b=[t.norm for t in got], autojunk=False)
    hit = [False] * len(want)
    for block in matcher.get_matching_blocks():
        for i in range(block.a, block.a + block.size):
            hit[i] = True
    words = [{"text": t.text, "ok": ok} for t, ok in zip(want, hit) if t.text]
    return {"score": round(100 * sum(hit) / len(want)), "words": words}


def count_words(text: str) -> int:
    return len(_WORD.findall(text))
