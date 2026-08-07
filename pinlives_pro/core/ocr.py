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


def remove_strikethrough(img: np.ndarray) -> np.ndarray:
    """Erase rules drawn across the text.

    A strikethrough runs far wider than any glyph stroke, so a wide horizontal
    opening isolates it. Inpainting fills it from neighbouring pixels instead of
    leaving a gap that reads as an extra character.
    """
    gray = _gray(img)
    span = max(gray.shape[1] // 4, 25)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (span, 1))

    mask = np.zeros(gray.shape, np.uint8)
    for polarity in (gray, 255 - gray):
        opened = cv2.morphologyEx(polarity, cv2.MORPH_OPEN, kernel)
        _, binary = cv2.threshold(opened, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        mask = cv2.bitwise_or(mask, binary)

    # A mask covering most of the frame is background, not a line.
    if mask.sum() == 0 or mask.mean() > 60:
        return img

    mask = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    src = img if img.ndim == 3 else cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    return cv2.inpaint(src, mask, 3, cv2.INPAINT_TELEA)


def _upscale(img: np.ndarray, factor: float) -> np.ndarray:
    if factor == 1.0:
        return img
    h, w = img.shape[:2]
    # Tesseract wants roughly 30px glyph height; small screenshots need the lift.
    return cv2.resize(img, (int(w * factor), int(h * factor)), interpolation=cv2.INTER_CUBIC)


def _otsu(gray: np.ndarray, invert: bool) -> np.ndarray:
    _, out = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return 255 - out if invert else out


def colour_key(img: np.ndarray, s_max: int, v_min: int) -> np.ndarray:
    """Isolate near-white glyphs by colour, returned as dark text on white.

    Measured on real posts, this is what separates a readable crop from noise.
    Codes are drawn in white with a dark outline over a saturated banner, and a
    grayscale threshold merges that outline into the background: the same crop
    read as `i B7es 2` through Otsu and `yNbEBTeSNa` through this key, against a
    truth of `yNbEB7eSNa`. Low saturation plus high value keeps the glyph body
    and drops both the outline and the banner.
    """
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV) if img.ndim == 3 else None
    if hsv is None:
        return img
    mask = cv2.inRange(hsv, (0, 0, v_min), (179, s_max, 255))
    return 255 - mask


def colour_key_dark(img: np.ndarray, v_max: int = 90) -> np.ndarray:
    """Isolate near-black glyphs, for codes drawn dark on a bright banner."""
    if img.ndim != 3:
        return img
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (0, 0, 0), (179, 255, v_max))
    return 255 - mask


def build_variants(img: np.ndarray) -> List[Tuple[str, np.ndarray, int]]:
    """Preprocessing variants, ordered by how often they carry the answer.

    PSM 6 is deliberately absent: measured at 77s on a 603x697 screenshot here,
    against 3s for PSM 7 on the same input. Upscaling before a whole-image pass
    is absent for the same reason — it multiplies an already slow pass.
    """
    clean = remove_strikethrough(img)
    gray = _gray(clean)

    variants = [
        # Colour keys first: they carry the answer on real banner posts.
        ('white_key_80',   colour_key(clean, 80, 170),   PSM_SPARSE),
        ('white_key_100',  colour_key(clean, 100, 160),  PSM_SPARSE),
        ('dark_key',       colour_key_dark(clean),       PSM_SPARSE),
        ('gray_sparse',    gray,                         PSM_SPARSE),
        ('otsu_inv',       _otsu(gray, True),            PSM_SPARSE),
    ]
    return variants


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
