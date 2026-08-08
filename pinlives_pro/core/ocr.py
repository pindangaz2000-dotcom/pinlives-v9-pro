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
from concurrent.futures import TimeoutError as FuturesTimeout
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

# Per-variant deadline. A healthy tesseract reads a code crop in well under a
# second; a slow host is a signal to surface, not to wait minutes on.
import os as _os
VARIANT_TIMEOUT_S = float(_os.environ.get('OCR_VARIANT_TIMEOUT_S', '15'))

# Latency levers learned from an on-device (Lens-style) image pipeline and then
# measured here, not assumed:
#   - the code banners are horizontal, so the angle-classifier model is a wasted
#     pass — dropping it cut ~25% of latency with no accuracy change;
#   - processing at the smallest sufficient resolution trims more; only oversized
#     images are shrunk, so the multi-code posts keep their small glyphs.
# (Raising onnx thread count was also tried and *rejected* — it was slower here.)
OCR_MAX_SIDE = int(_os.environ.get('OCR_MAX_SIDE', '1600'))


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
    # Observed in the PP-OCRv4 benchmark on real posts:
    'H': 'I', 'I': 'H',   # rS2HNFvDME read as rS2INFvDME
    'c': 'e', 'e': 'c',   # kbs7cox3AU read as kbs7e0x3AU
    'k': 'h', 'h': 'k',   # E8kkYruH8t read as C8hhYruH8t
    'E': 'C', 'C': 'E',
}

# Letters whose upper and lower case are near-identical in shape, so OCR guesses
# the case from height alone and often gets it wrong on a short code. Case is
# meaningful in these codes, so both cases are offered for the site to arbitrate.
_CASE_AMBIGUOUS = 'coskpuvwxz'
CASE_PAIRS = {}
for _ch in _CASE_AMBIGUOUS:
    CASE_PAIRS[_ch] = _ch.upper()
    CASE_PAIRS[_ch.upper()] = _ch

MAX_CONFUSION_SWAPS = 2


def _alnum_substrings(token: str, min_len: int = MIN_CODE_LEN):
    """Contiguous substrings long enough to be a code, longest first.

    Detection occasionally fuses a code with an adjacent number (a balance, a
    'x2' multiplier); the real code is a substring of the fused token.
    """
    n = len(token)
    if n <= min_len or n > 24:
        return []
    seen = set()
    out = []
    for length in range(n - 1, min_len - 1, -1):
        for i in range(0, n - length + 1):
            sub = token[i:i + length]
            if sub != token and sub not in seen:
                seen.add(sub)
                out.append(sub)
    return out[:12]


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
                for swap in (CONFUSIONS.get(ch), CASE_PAIRS.get(ch)):
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

# Recognition model, chosen by measurement on the real posts rather than by
# recency. On single-code images: v4 Chinese rec read 4/9, v5 Chinese 6/9, and
# the ENGLISH rec model 7/9 — the codes are Latin script, so the language of the
# recogniser mattered more than the version. v4-en and v5-en each read a
# different 7/9, and their union is 9/9, so an ensemble of the two is offered.
OCR_REC_LANG = _os.environ.get('OCR_REC_LANG', 'en')          # 'en' or 'ch'
OCR_MODEL_VERSION = _os.environ.get('OCR_MODEL_VERSION', 'PP-OCRv4')
OCR_ENSEMBLE = _os.environ.get('OCR_ENSEMBLE', 'false').lower() == 'true'
OCR_RECOVERY_SWAPS = int(_os.environ.get('OCR_RECOVERY_SWAPS', '2'))


def _build_rapid(version: str, lang: str):
    """A RapidOCR 3.x reader for a specific model version + recognition language."""
    from rapidocr import RapidOCR, OCRVersion, ModelType, LangDet, LangRec
    ver = {'PP-OCRv4': OCRVersion.PPOCRV4, 'PP-OCRv5': OCRVersion.PPOCRV5,
           'PP-OCRv6': OCRVersion.PPOCRV6}[version]
    rec_lang = {'en': LangRec.EN, 'ch': LangRec.CH}[lang]
    return RapidOCR(params={
        'Det.ocr_version': ver, 'Det.model_type': ModelType.MOBILE, 'Det.lang_type': LangDet.CH,
        'Rec.ocr_version': ver, 'Rec.model_type': ModelType.MOBILE, 'Rec.lang_type': rec_lang,
        'Global.use_cls': False,
    })


def _load_rapidocr():
    """Return the OCR reader(s), preferring the measured-best configuration.

    RapidOCR 3.x with the English rec model is the primary; with OCR_ENSEMBLE a
    second (v5-en) reader is added and their readings merged. Falls back to the
    older bundled package, then to None (tesseract)."""
    try:
        readers = [_build_rapid(OCR_MODEL_VERSION, OCR_REC_LANG)]
        if OCR_ENSEMBLE:
            other = 'PP-OCRv5' if OCR_MODEL_VERSION != 'PP-OCRv5' else 'PP-OCRv4'
            try:
                readers.append(_build_rapid(other, OCR_REC_LANG))
            except Exception as e:
                logger.warning("Ensemble second model unavailable: %s", e)
        return readers
    except Exception as e:
        logger.info("RapidOCR 3.x unavailable (%s); trying bundled package", e)
    try:
        from rapidocr_onnxruntime import RapidOCR
        return [RapidOCR()]
    except Exception as e:
        logger.info("RapidOCR unavailable, falling back to tesseract: %s", e)
        return None


def _normalise_result(result) -> List[Tuple[str, float]]:
    """Flatten either RapidOCR API to (text, score) pairs.

    3.x returns an object with .txts/.scores; 1.4.x returns (list-of-[box,text,
    score], elapse)."""
    pairs: List[Tuple[str, float]] = []
    if result is None:
        return pairs
    txts = getattr(result, 'txts', None)
    if txts is not None:  # 3.x result object
        scores = getattr(result, 'scores', None) or [0.0] * len(txts)
        for t, s in zip(txts, scores):
            try:
                pairs.append((t or '', float(s) * 100))
            except (TypeError, ValueError):
                pairs.append((t or '', 0.0))
        return pairs
    if isinstance(result, (list, tuple)):  # 1.4.x (list, elapse) or bare list
        rows = result[0] if (len(result) == 2 and isinstance(result[0], list)) else result
        for item in rows or []:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                score = float(item[2]) * 100 if len(item) > 2 else 0.0
                pairs.append((item[1] or '', score))
    return pairs


class GiftcodeOCR:
    """Code OCR. Primary reader is PP-OCR (RapidOCR); tesseract is the fallback."""

    def __init__(self, cache_size: int = 256, max_workers: int = 5):
        self._cache: OrderedDict = OrderedDict()
        self._cache_size = cache_size
        self._pool = ThreadPoolExecutor(max_workers=max_workers)
        self._rapid = _load_rapidocr()
        self._io_readers = None  # lazy v4+v5 ensemble for image-only sites

    @property
    def backend(self) -> str:
        if not self._rapid:
            return 'tesseract'
        n = len(self._rapid)
        return f'rapidocr-{OCR_MODEL_VERSION}-{OCR_REC_LANG}' + (f'-ensemble{n}' if n > 1 else '')

    @staticmethod
    def _cap_resolution(img: np.ndarray) -> np.ndarray:
        """Shrink only oversized images to the smallest sufficient side length."""
        h, w = img.shape[:2]
        longest = max(h, w)
        if longest <= OCR_MAX_SIDE:
            return img
        s = OCR_MAX_SIDE / longest
        return cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)

    def _rapid_words(self, img: np.ndarray, readers=None) -> List[Tuple[str, float]]:
        """(token, confidence 0-100) for every alnum word PP-OCRv4 detects.

        Detection sometimes merges an adjacent balance/label into a code
        (`10092JtVzWvYrF`), so substrings of a long token are offered too — the
        site validator picks the real code out of them.

        readers overrides the reader set (used by the image-only ensemble).
        """
        img = self._cap_resolution(img)
        pairs: List[Tuple[str, float]] = []
        for reader in (readers or self._rapid):
            try:
                pairs.extend(_normalise_result(reader(img)))
            except Exception as e:
                logger.warning("RapidOCR reader failed: %s: %s", type(e).__name__, e)

        out: List[Tuple[str, float]] = []
        for text, score in pairs:
            for piece in (text or '').replace('\n', ' ').split():
                tok = _NON_ALNUM.sub('', piece)
                if tok:
                    out.append((tok, score))
                    # Recover a code fused with neighbouring text.
                    for sub in _alnum_substrings(tok):
                        out.append((sub, score * 0.9))
        return out

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

    @staticmethod
    def _image_only_regions(img: np.ndarray) -> List[np.ndarray]:
        """Grayscale full frame plus a centre-dropped two-column crop.

        8KBET-style posts put the codes in side columns around a hero photo; the
        noisy centre makes the detector miss edge codes. OCR'ing the columns as
        well as the full frame recovers those, and — because the results are
        unioned with the full-frame pass — a code is never lost even if the crop
        misjudges the layout. Measured 15 -> 17 of 20 on a real 8KBET image.

        Otsu thresholding and colour masking were measured to destroy this
        yellow-on-photo text (Otsu 4/20, colour-mask 0/20) and are not used.
        """
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        gray3 = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        h, w = gray.shape[:2]
        if w < 200:  # too narrow to have side columns; the full frame is enough
            return [gray3]
        cols = np.hstack([gray3[:, :int(w * 0.30)], gray3[:, int(w * 0.70):]])
        return [gray3, cols]

    def _image_only_readers(self):
        """The reader set for image-only OCR: the primary plus the other PP-OCR
        version, built once. The two versions disagree on different confusable
        glyphs, so unioning their reads recovers codes neither gets alone. Falls
        back to the primary reader(s) if the second model cannot be built."""
        if not self._rapid:
            return self._rapid
        if self._io_readers is None:
            readers = list(self._rapid)
            other = 'PP-OCRv5' if OCR_MODEL_VERSION != 'PP-OCRv5' else 'PP-OCRv4'
            try:
                readers.append(_build_rapid(other, OCR_REC_LANG))
            except Exception as e:
                logger.warning("Image-only ensemble second model unavailable: %s", e)
            self._io_readers = readers
        return self._io_readers

    def _extract_rapid(self, img, validator, max_codes, start,
                       image_only: bool = False) -> 'OCRResult':
        """Read with PP-OCRv4, then rank by confidence and validate.

        When a reading is one confusable glyph from a code the site accepts, the
        corrected form is offered too — this is where the H/I, c/e, 7/T class of
        single-glyph errors gets recovered, with the site format as the arbiter.

        image_only reads the full frame plus a two-column crop and unions the
        words, for sites whose codes sit in side columns around a photo.
        """
        if image_only:
            # Different model versions make different single-glyph confusions, so
            # a v4+v5 ensemble over the two regions recovers codes neither reads
            # alone (measured 16 -> 17 of 20 on a real 8KBET image).
            readers = self._image_only_readers()
            words: List[Tuple[str, float]] = []
            for region in self._image_only_regions(img):
                words.extend(self._rapid_words(region, readers=readers))
        else:
            words = self._rapid_words(img)
        best_conf: Dict[str, float] = {}

        def consider(tok: str, conf: float):
            if self._plausible(tok) and conf > best_conf.get(tok, -1):
                best_conf[tok] = conf

        for token, conf in words:
            if not self._plausible(token):
                continue
            if validator is not None and validator(token):
                consider(token, conf)
            elif validator is not None:
                # Near-miss recovery, gated by the site validator. Two swaps,
                # because a single reading can carry two independent glyph errors
                # at once — measured: `rS2INFVDME` needs both I→H and V→v to
                # reach `rS2HNFvDME`. The validator makes the wider search safe.
                for alt in confusion_candidates(token, max_swaps=OCR_RECOVERY_SWAPS):
                    if validator(alt):
                        consider(alt, conf * 0.8)
                        break  # nearest-first; take the closest accepted reading
            else:
                consider(token, conf)

        codes = [OCRCode(tok, 1, c, ['rapidocr']) for tok, c in best_conf.items()]
        codes.sort(key=lambda c: c.confidence, reverse=True)
        return OCRResult(codes[:max_codes], (time.perf_counter() - start) * 1000, len(words))

    def extract(
        self,
        image_input,
        validator: Optional[Callable[[str], bool]] = None,
        max_codes: int = 50,
        image_only: bool = False,
    ) -> OCRResult:
        """Read every code in an image.

        validator: returns True when a candidate matches the expected site
        format. Supplying it is what separates a code from surrounding UI text —
        a screenshot is full of words that pass a generic shape check.

        image_only: also OCR a two-column crop and union the readings, for sites
        (8KBET) whose codes sit in side columns around a photo.
        """
        start = time.perf_counter()
        img = self._load(image_input)
        if img is None:
            return OCRResult([], (time.perf_counter() - start) * 1000, 0)

        key = hashlib.sha256(np.ascontiguousarray(img).tobytes()).hexdigest()
        if image_only:
            key += ':io'
        if key in self._cache:
            cached = self._cache[key]
            self._cache.move_to_end(key)
            return OCRResult(cached.codes, (time.perf_counter() - start) * 1000,
                             cached.words_seen)

        # Primary path: PP-OCR. One model does detection + recognition, so it
        # finds every code region and reads it in one fast pass.
        if self._rapid:
            result = self._extract_rapid(img, validator, max_codes, start,
                                         image_only=image_only)
            self._store(key, result)
            return result

        variants = build_variants(img)
        futures = {
            name: self._pool.submit(self._words, image, psm)
            for name, image, psm in variants
        }

        votes: Dict[str, int] = defaultdict(int)
        conf_sum: Dict[str, float] = defaultdict(float)
        sources: Dict[str, List[str]] = defaultdict(list)
        words_seen = 0

        timed_out = 0
        for name, future in futures.items():
            try:
                words = future.result(timeout=VARIANT_TIMEOUT_S)
            except FuturesTimeout:
                timed_out += 1
                logger.warning("OCR variant %s exceeded %.0fs — tesseract is slow "
                               "on this host", name, VARIANT_TIMEOUT_S)
                continue
            except Exception as e:
                logger.warning("OCR variant %s failed: %s: %s", name, type(e).__name__, e)
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

        if timed_out:
            logger.warning('OCR: %d/%d variants timed out', timed_out, len(variants))
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
