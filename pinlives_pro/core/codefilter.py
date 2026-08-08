"""
PINLIVES Pro v3.1 - Code extraction and validation.

Pulls candidate codes out of message text and rejects the noise that dominates
real channels: dates, phone numbers, prices, URLs, and repeated-character runs.
Ranking uses Shannon entropy, which separates a real code from a filler string
of the same length.
"""

import math
import re
from collections import Counter
from typing import List, Set

# A code is an alphanumeric run of 5-32 chars; separators inside are kept out so
# "AB7X-9Q2M" yields both halves rather than one unusable blob.
_CANDIDATE_RE = re.compile(r'[A-Za-z0-9]{5,32}')

# Contexts that must never be mined for codes.
_URL_RE = re.compile(r'https?://\S+|www\.\S+|\S+@\S+\.\S+')

# Shapes that look code-like but never are.
_DATE_RE = re.compile(r'^\d{1,4}[-/.]?\d{1,2}[-/.]?\d{1,4}$')
_ALL_DIGITS_RE = re.compile(r'^\d+$')

_STOPWORDS: Set[str] = {
    'HTTPS', 'HTTP', 'TELEGRAM', 'CHANNEL', 'GROUP', 'ADMIN', 'MEMBER',
    'MESSAGE', 'FORWARD', 'SUBSCRIBE', 'PLEASE', 'THANK', 'THANKS',
    'PASSWORD', 'USERNAME', 'ACCOUNT', 'UPDATE', 'VERSION', 'DOWNLOAD',
    'CLICK', 'HERE', 'TODAY', 'TOMORROW', 'YESTERDAY',
}

MIN_ENTROPY = 2.0
MIN_LENGTH = 5
MAX_LENGTH = 32
MAX_REPEAT_RATIO = 0.5


def shannon_entropy(text: str) -> float:
    """Bits of entropy per character. 'AAAAAA' scores 0, a real code scores ~2.5+."""
    if not text:
        return 0.0
    counts = Counter(text)
    length = len(text)
    return -sum((c / length) * math.log2(c / length) for c in counts.values())


def repeat_ratio(text: str) -> float:
    """Share of the string taken by its single most common character."""
    if not text:
        return 1.0
    return Counter(text).most_common(1)[0][1] / len(text)


def is_valid_code(candidate: str) -> bool:
    """Whether a candidate survives the garbage filters."""
    if not candidate:
        return False
    if not (MIN_LENGTH <= len(candidate) <= MAX_LENGTH):
        return False
    if candidate.upper() in _STOPWORDS:
        return False
    if _DATE_RE.match(candidate):
        return False
    # Pure digit runs are phone numbers, prices, timestamps — not codes.
    if _ALL_DIGITS_RE.match(candidate):
        return False
    # A code mixes cases or mixes letters with digits; a plain lowercase word does not.
    has_digit = any(c.isdigit() for c in candidate)
    has_alpha = any(c.isalpha() for c in candidate)
    if not (has_digit and has_alpha):
        return False
    if repeat_ratio(candidate) > MAX_REPEAT_RATIO:
        return False
    if shannon_entropy(candidate) < MIN_ENTROPY:
        return False
    return True


def extract_codes(text: str) -> List[str]:
    """Return the valid codes in a message, in order, without duplicates."""
    if not text:
        return []

    # Strip URLs and emails first so their path segments are not mined.
    cleaned = _URL_RE.sub(' ', text)

    seen: Set[str] = set()
    found: List[str] = []
    for match in _CANDIDATE_RE.finditer(cleaned):
        candidate = match.group(0)
        if candidate in seen:
            continue
        if is_valid_code(candidate):
            seen.add(candidate)
            found.append(candidate)
    return found
