"""Per-site code-length rules, applied on top of the site extractors.

The vendored filters.py does not enforce every site's exact code length, so a
short ordinary word can slip through — qq88 accepted 'Please' (6 chars) though a
qq88 code is always 10. These are the lengths there is direct evidence for
(v9.5's rules plus live posts); a site not listed here is left untouched so real
codes on other sites are never rejected.
"""

from typing import Optional

# site -> (min_len, max_len), inclusive.
SITE_CODE_LENGTHS = {
    'qq88': (10, 10),   # exactly 10 (v9.5 is_clean_code; live cMoC1cCsy3)
    '8kbet': (8, 8),    # exactly 8, a-zA-Z0-9 (operator spec; live image codes)
    'rr88': (6, 6),     # KJC sites: exactly 6 (v9.5 health KJC666; live J72LIS…)
    'mm88': (6, 6),
    'xx88': (6, 6),
    'gg88': (6, 6),
    'llwin': (6, 6),
}


def passes_length(site: Optional[str], code: str) -> bool:
    """True if the code's length is allowed for the site (or the site has no rule)."""
    rng = SITE_CODE_LENGTHS.get((site or '').strip().lower())
    if not rng:
        return True
    return rng[0] <= len(code) <= rng[1]


# Sites that publish their codes only inside images. Their message text is
# promo copy (links, spoilers, amounts) with no code, so running the text
# extractor on it only produces false positives — the code comes from OCR of the
# attached image instead. 8KBET posts its codes 100% on images.
IMAGE_ONLY_SITES = {'8kbet'}


def is_image_only(site: Optional[str]) -> bool:
    """True if the site's codes live only in images, so message text is skipped."""
    return (site or '').strip().lower() in IMAGE_ONLY_SITES
