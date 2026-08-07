"""Routing for kjc_shared aggregate channels.

A kjc_shared channel (kjc_gaixinh2, kjc_fifa_worldcup, stthaymoingay) posts
codes for several KJC sites at once. v9.5's listener picked the concrete site
from a marker in the post ("MM88", "RR88", ...) and, when no marker was present,
treated the code as valid for all five. This reproduces that resolution so the
correct per-site validator runs instead of the generic filter.

The channel's stored site stays 'kjc_shared'; resolution happens at extraction
time from the post text. Keeping it that way means a journal replay re-runs the
exact same routing rather than a frozen guess.
"""

from typing import List, Optional

KJC_SHARED = 'kjc_shared'

# The sites a kjc_shared post can target, and the order used when no marker is
# present (a code is then treated as valid for all of them).
KJC_SITES = ('rr88', 'mm88', 'xx88', 'gg88', 'llwin')

# Marker → site, in v9.5's precedence order. First marker found in the post wins.
_MARKERS = (
    ('MM88', 'mm88'),
    ('RR88', 'rr88'),
    ('LLWIN', 'llwin'),
    ('XX88', 'xx88'),
    ('GG88', 'gg88'),
)


def detect_kjc_site_from_text(text: str) -> Optional[str]:
    """The concrete KJC site named in the post, or None when none is named."""
    if not text:
        return None
    upper = text.upper()
    for marker, site in _MARKERS:
        if marker in upper:
            return site
    return None


def resolve_sites(site_id: Optional[str], text: str) -> List[str]:
    """Concrete site(s) a message's codes belong to.

    A normal site resolves to itself. A kjc_shared channel resolves to the one
    site named in the post, or to all five KJC sites when none is named. An
    empty site resolves to no site (the caller then uses the generic filter).
    """
    site = (site_id or '').strip().lower()
    if site != KJC_SHARED:
        return [site] if site else []
    detected = detect_kjc_site_from_text(text)
    return [detected] if detected else list(KJC_SITES)
