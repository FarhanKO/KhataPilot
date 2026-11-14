"""Tidying what speech-to-text heard, before it goes into the message box (the owner still reads it and
presses send). Pure functions.

- The words a shop's books live on: "bikash" is bKash, "nogod" is Nagad, "backy" is baki.
- Amounts said in words become digits ("five thousand five hundred taka" is "5500 taka"), so the offline
  planner reads them too.
- A name heard slightly wrong snaps to the customer or supplier the books know ("Jahangir Allam" is
  "Jahangir Alam"), only when it is very close: a guessed name is worse than a misspelt one.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

BRANDS = [
    (re.compile(r"\b(?:b[\s.-]?kash|bi[\s-]?kash|bekash|be[\s-]?cash|b[\s-]?cash|bkas)\b", re.I), "bKash"),
    (re.compile(r"\b(?:nogod|nagod|nogot|nagat|nugget)\b", re.I), "Nagad"),
    (re.compile(r"\b(?:backy|bakhi|bucky|baakie?|baaki|backi|bakki|bacchi|baqi)\b", re.I), "baki"),
    (re.compile(r"\b(?:takka|taaka|tucka|takah)\b", re.I), "taka"),
]
UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
         "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
         "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
         "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "a": 1}
SCALES = {"hundred": 100, "thousand": 1_000, "hajar": 1_000, "hazar": 1_000, "lakh": 100_000, "lac": 100_000,
          "lakhs": 100_000, "crore": 10_000_000}
NUMBER_WORD = "|".join(sorted({*UNITS, *SCALES, "and"}, key=len, reverse=True))
NUMBER_RUN = re.compile(rf"\b(?:(?:{NUMBER_WORD}|\d+)[\s-]+)*(?:{NUMBER_WORD})\b", re.I)


def _words_to_number(words: list[str]) -> int | None:
    total = current = 0
    seen = False
    for word in words:
        if word == "and":
            continue
        if word.isdigit():
            current += int(word)
            seen = True
        elif word in UNITS:
            if word == "a" and seen:
                return None
            current += UNITS[word]
            seen = True
        elif word in SCALES:
            scale = SCALES[word]
            if scale == 100:
                current = max(current, 1) * 100
            else:
                total += max(current, 1) * scale
                current = 0
            seen = True
        else:
            return None
    return total + current if seen else None


def _amounts(text: str) -> str:
    def swap(match: re.Match[str]) -> str:
        words = [w for w in re.split(r"[\s-]+", match.group(0).lower()) if w]
        if words in (["a"], ["and"]) or all(w in ("a", "and") for w in words):
            return match.group(0)
        if not any(w in SCALES for w in words) and not any(w.isdigit() for w in words) and len(words) == 1:
            return match.group(0)  # "one", "two": often not an amount ("one customer")
        value = _words_to_number(words)
        return str(value) if value is not None else match.group(0)

    return NUMBER_RUN.sub(swap, text)


def _snap_names(text: str, names: list[str]) -> str:
    """Replace a run of words that is almost exactly a known name (one or more words) with that name."""
    for name in sorted({n for n in names if n and len(n) >= 4}, key=len, reverse=True):
        size = len(name.split())
        tokens = text.split()
        for i in range(len(tokens) - size + 1):
            window = " ".join(tokens[i:i + size])
            core = re.sub(r"^[^\w]+|[^\w]+$", "", window)
            if not core or core == name:
                continue
            if SequenceMatcher(None, core.lower(), name.lower()).ratio() >= (0.86 if size > 1 else 0.9):
                tokens[i:i + size] = [window.replace(core, name)]
                text = " ".join(tokens)
                break
    return text


def tidy(text: str, names: list[str] | None = None) -> str:
    out = " ".join(text.split())
    for pattern, word in BRANDS:
        out = pattern.sub(word, out)
    out = _amounts(out)
    out = re.sub(r"(\d)\s*,\s*(\d{3})\b", r"\1\2", out)  # "5, 000" -> "5000"
    if names:
        out = _snap_names(out, names)
    return out.strip()


def prompt_for(names: list[str], *, bangla: bool) -> tuple[str, str]:
    """(the initial prompt, hotwords) that steer the model toward this shop's words and names. Whisper reads
    at most about 224 tokens of prompt, so the names are capped."""
    listed = ", ".join(names[:40])
    if bangla:
        prompt = f"{listed}. বাকি, টাকা, বিকাশ, নগদ, বিক্রি, খরচ। রহিম ট্রেডার্সকে ৫০০০ টাকা দাও। বাবুলের বাকি কত?"
    else:
        prompt = (f"{listed}. baki, taka, bKash, Nagad, bikri, khoroch, joma. Rahim Traders ke 5000 taka bKash e "
                  "pathao. Babul er baki koto? Sold rice 1500 taka cash. Send baki reminder to Babul and Sumi.")
    return prompt, "bKash Nagad baki taka"
