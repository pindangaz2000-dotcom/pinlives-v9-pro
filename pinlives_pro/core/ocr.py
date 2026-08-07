"""
PINLIVES Pro v3.1 - Giftcode OCR.

Real posts are screenshots, not clean crops: a code sits in a coloured banner
over a game screen or a photograph, sometimes struck through, and a single post
can carry twenty codes laid out in columns. So this reads the whole image as
sparse text, keeps every word that could be a code, and confirms each against
the site's own format.

Two approaches were rejected after measuring them:

* Single-line OCR (PSM 7) assumes one code per image and returns one string. It
  cannot express the twenty-code layout at all.
* Positional character rules. An earlier engine hardcoded corrections for one
  sample ({2:'Q', 5:'B', 6:'J', 7:'L'} for a code reading `twQkyBJLql`). It
  scored perfectly on that sample and corrupted anything sharing the prefix:
  `twXbZbJjLo` came back as `twXbZBJjLq`. Agreement across variants plus format
  validation generalises; a fitted table does not.
"""

import hashlib
import logging
import re
import time
from collections import OrderedDict, defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np
import pytesseract

logger = logging.getLogger(__name__)

_WHITELIST = (
    'abcdefghijklmnopqrstuvwxyz'
    'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    '0123456789'
)
# 11 = sparse text: find words anywhere, no layout assumption.
# 6  = uniform block: better when codes are stacked in a column.
PSM_SPARSE = 11
PSM_BLOCK = 6

_NON_ALNUM = re.compile(r'[^A-Za-z0-9]')

MIN_CODE_LEN = 6
MAX_CODE_LEN = 16
MIN_WORD_CONF = 30.0


def _config(psm: int) -> str:
    return f'--oem 1 --psm {psm} -c tessedit_char_whitelist={_WHITELIST}'


# ----------------------------------------------------------------------
# Preprocessing
# ----------------------------------------------------------------------

def _gray(img: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img


def preprocess_screenshot(
    img: np.ndarray,
    binarize_threshold: int = 125,
    scale: int = 2,
    blue_filter: bool = True,
    strike_filter: bool = True,
    restoration: bool = True,
    sharpen: bool = True,
) -> np.ndarray:
    """Segment a code out of a game screenshot.

    Ported from the v9.5 TypeScript preprocessor and vectorised. The part worth
    keeping is stroke restoration: inpainting a strikethrough blurs the glyph it
    crosses, so instead each struck pixel is tested for bright text 1-2px above
    AND below, or left AND right. Text on both sides means the rule is crossing
    a stroke, and the pixel is rebuilt as text; otherwise it is erased.

    Measured against an HSV colour key on a real post, both read 9 of 10
    characters. This one additionally handles the struck-through posts.
    """
    if img.ndim != 3:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    b, g, r = cv2.split(img.astype(np.int16))

    # Blue-heavy banner, the common backdrop in these screenshots.
    is_blue = ((b > 1.1 * r) & (b > 1.1 * g)) | ((b > 90) & (r < 120) & (g < 120))
    # Red/orange rule drawn across the code.
    is_red = ((r > 1.35 * g) & (r > 1.35 * b)) | ((r > 130) & (g < 100) & (b < 100))

    luma = 0.299 * r + 0.587 * g + 0.114 * b
    bright = (luma > binarize_threshold)
    if strike_filter:
        bright = bright & ~is_red

    out = np.where(bright, 255, 0).astype(np.uint8)
    if blue_filter:
        out[is_blue] = 0

    if strike_filter:
        if restoration:
            def shifted(mask, dy, dx):
                return np.roll(np.roll(mask, dy, axis=0), dx, axis=1)

            above = shifted(bright, 1, 0) | shifted(bright, 2, 0)
            below = shifted(bright, -1, 0) | shifted(bright, -2, 0)
            left = shifted(bright, 0, 1) | shifted(bright, 0, 2)
            right = shifted(bright, 0, -1) | shifted(bright, 0, -2)
            repair = is_red & ((above & below) | (left & right))
            out[is_red] = 0
            out[repair] = 255
        else:
            out[is_red] = 0

    if sharpen:
        out = cv2.filter2D(out, -1, np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], np.float32))

    if scale > 1:
        # Nearest neighbour: the image is binary now, and interpolating only
        # softens the edges tesseract depends on.
        out = cv2.resize(out, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    return out


# Glyph pairs tesseract confuses in these fonts. Measured: a real code reading
# `yNbEB7eSNa` comes back as `yNbEBTeSNa`, with word confidence reported as 0 —
# tesseract gives no signal that it was uncertain, so candidates are generated
# and the site validator decides. This cannot rescue a code whose alternatives
# are all valid for the site; it recovers the ones where the format disagrees.
CONFUSIONS = {
    '7': 'T', 'T': '7',
    '0': 'O', 'O': '0',
    '1': 'l', 'l': '1', 'I': '1',
    '5': 'S', 'S': '5',
    '8': 'B', 'B': '8',
    '2': 'Z', 'Z': '2',
    '6': 'G', 'G': '6',
    '9': 'g', 'g': '9',
}

MAX_CONFUSION_SWAPS = 2


def confusion_candidates(code: str, max_swaps: int = MAX_CONFUSION_SWAPS) -> List[str]:
    """Readings reachable by swapping up to `max_swaps` confusable glyphs.

    Ordered nearest-first, so a validator that accepts several sees the reading
    closest to what was actually on screen.
    """
    results: List[str] = []
    seen = {code}
    frontier = [code]
    for _ in range(max_swaps):
        nxt = []
        for candidate in frontier:
            for i, ch in enumerate(candidate):
                swap = CONFUSIONS.get(ch)
                if not swap:
                    continue
                alt = candidate[:i] + swap + candidate[i + 1:]
                if alt in seen:
                    continue
                seen.add(alt)
                results.append(alt)
                nxt.append(alt)
        frontier = nxt
        if not frontier:
            break
    return results


def colour_key(img: np.ndarray, s_max: int, v_min: int) -> np.ndarray:
    """Isolate near-white glyphs by colour, as dark text on white."""
    if img.ndim != 3:
        return img
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    return 255 - cv2.inRange(hsv, (0, 0, v_min), (179, s_max, 255))


def colour_key_dark(img: np.ndarray, v_max: int = 90) -> np.ndarray:
    """Isolate near-black glyphs, for codes drawn dark on a bright banner."""
    if img.ndim != 3:
        return img
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    return 255 - cv2.inRange(hsv, (0, 0, 0), (179, 255, v_max))


def _otsu(gray: np.ndarray, invert: bool) -> np.ndarray:
    _, out = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return 255 - out if invert else out


def build_variants(img: np.ndarray) -> List[Tuple[str, np.ndarray, int]]:
    """Preprocessing variants, ordered by how often they carry the answer.

    PSM 6 is deliberately absent: measured at 77s on a 603x697 screenshot,
    against 3s for PSM 7 on the same input. Whole-image upscaling is absent for
    the same reason.
    """
    gray = _gray(img)
    return [
        ('v95_thr125_x2',  preprocess_screenshot(img, 125, 2),  PSM_SPARSE),
        ('v95_thr100_x2',  preprocess_screenshot(img, 100, 2),  PSM_SPARSE),
        ('white_key_100',  colour_key(img, 100, 160),           PSM_SPARSE),
        ('dark_key',       colour_key_dark(img),                PSM_SPARSE),
        ('otsu_inv',       _otsu(gray, True),                   PSM_SPARSE),
    ]


# ----------------------------------------------------------------------
# Results
# ----------------------------------------------------------------------

class OCRCode:
    def __init__(self, code: str, votes: int, confidence: float, sources: List[str]):
        self.code = code
        self.votes = votes
        self.confidence = confidence
        self.sources = sources

    def to_dict(self) -> Dict[str, Any]:
        return {
            'code': self.code,
            'votes': self.votes,
            'confidence': round(self.confidence, 1),
            'sources': self.sources,
        }

    def __repr__(self):
        return f"OCRCode({self.code!r}, votes={self.votes}, conf={self.confidence:.0f})"


class OCRResult:
    def __init__(self, codes: List[OCRCode], elapsed_ms: float, words_seen: int):
        self.codes = codes
        self.elapsed_ms = elapsed_ms
        self.words_seen = words_seen

    @property
    def best(self) -> str:
        return self.codes[0].code if self.codes else ''

    def to_dict(self) -> Dict[str, Any]:
        return {
            'codes': [c.to_dict() for c in self.codes],
            'elapsed_ms': round(self.elapsed_ms, 1),
            'words_seen': self.words_seen,
        }


# ----------------------------------------------------------------------
# Engine
# ----------------------------------------------------------------------

class GiftcodeOCR:
    """Sparse-text OCR over several preprocessing variants, merged by agreement."""

    def __init__(self, cache_size: int = 256, max_workers: int = 5):
        self._cache: OrderedDict = OrderedDict()
        self._cache_size = cache_size
        self._pool = ThreadPoolExecutor(max_workers=max_workers)

    def _words(self, image: np.ndarray, psm: int) -> List[Tuple[str, float]]:
        """Every word tesseract finds, with its confidence."""
        try:
            data = pytesseract.image_to_data(
                image, config=_config(psm), output_type=pytesseract.Output.DICT
            )
        except Exception as e:
            logger.warning("Tesseract failed (psm=%s): %s: %s", psm, type(e).__name__, e)
            return []

        out = []
        for text, conf in zip(data.get('text', []), data.get('conf', [])):
            token = _NON_ALNUM.sub('', text or '')
            if not token:
                continue
            try:
                confidence = float(conf)
            except (TypeError, ValueError):
                confidence = -1.0
            out.append((token, confidence))
        return out

    @staticmethod
    def _plausible(token: str) -> bool:
        """Cheap shape gate applied before the site validator."""
        if not (MIN_CODE_LEN <= len(token) <= MAX_CODE_LEN):
            return False
        if not token.isalnum():
            return False
        if token.isdigit():
            return False
        return True

    def extract(
        self,
        image_input,
        validator: Optional[Callable[[str], bool]] = None,
        max_codes: int = 50,
    ) -> OCRResult:
        """Read every code in an image.

        validator: returns True when a candidate matches the expected site
        format. Supplying it is what separates a code from surrounding UI text —
        a screenshot is full of words that pass a generic shape check.
        """
        start = time.perf_counter()
        img = self._load(image_input)
        if img is None:
            return OCRResult([], (time.perf_counter() - start) * 1000, 0)

        key = hashlib.sha256(np.ascontiguousarray(img).tobytes()).hexdigest()
        if key in self._cache:
            cached = self._cache[key]
            self._cache.move_to_end(key)
            return OCRResult(cached.codes, (time.perf_counter() - start) * 1000,
                             cached.words_seen)

        variants = build_variants(img)
        futures = {
            name: self._pool.submit(self._words, image, psm)
            for name, image, psm in variants
        }

        votes: Dict[str, int] = defaultdict(int)
        conf_sum: Dict[str, float] = defaultdict(float)
        sources: Dict[str, List[str]] = defaultdict(list)
        words_seen = 0

        for name, future in futures.items():
            try:
                words = future.result(timeout=60)
            except Exception as e:
                logger.warning("Variant %s failed: %s", name, e)
                continue
            words_seen += len(words)
            # One vote per variant per token: a variant repeating a token in the
            # same image should not outweigh agreement between variants.
            seen_here = set()
            for token, confidence in words:
                if token in seen_here:
                    continue
                if not self._plausible(token):
                    continue
                if confidence >= 0 and confidence < MIN_WORD_CONF:
                    continue
                if validator is not None and not validator(token):
                    continue
                seen_here.add(token)
                votes[token] += 1
                conf_sum[token] += max(confidence, 0.0)
                sources[token].append(name)

        codes = [
            OCRCode(token, count, conf_sum[token] / count, sources[token])
            for token, count in votes.items()
        ]
        # Agreement across variants first, then tesseract's own confidence.
        codes.sort(key=lambda c: (c.votes, c.confidence), reverse=True)
        codes = codes[:max_codes]

        result = OCRResult(codes, (time.perf_counter() - start) * 1000, words_seen)
        self._store(key, result)
        return result

    @staticmethod
    def _load(image_input) -> Optional[np.ndarray]:
        if isinstance(image_input, (bytes, bytearray)):
            return cv2.imdecode(np.frombuffer(image_input, np.uint8), cv2.IMREAD_COLOR)
        if isinstance(image_input, str):
            return cv2.imread(image_input)
        return image_input

    def _store(self, key: str, result: OCRResult) -> None:
        self._cache[key] = result
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    def close(self):
        self._pool.shutdown(wait=False)


_engine: Optional[GiftcodeOCR] = None


def get_ocr() -> GiftcodeOCR:
    global _engine
    if _engine is None:
        _engine = GiftcodeOCR()
    return _engine
