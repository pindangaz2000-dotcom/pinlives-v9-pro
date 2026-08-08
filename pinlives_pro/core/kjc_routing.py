"""Routing for kjc_shared aggregate channels.

A kjc_shared channel (kjc_gaixinh2, kjc_thethao, kjc_fifa_worldcup,
stthaymoingay) posts codes for several KJC sites at once. Which site a post
targets is read from the site markers in its text:

  * exactly one KJC site named  -> that site;
  * several sites named, or none -> all five (the code is a shared KJC code,
    valid across rr88/mm88/xx88/gg88/llwin).

Real KJC_THETHAO posts tag every KJC site (#rr88 #mm88 #xx88 #gg88 #LLwin), so
"first marker wins" would mis-route the whole channel to one site; counting the
distinct sites named is what makes an all-sites broadcast resolve to all five.

The channel's stored site stays 'kjc_shared'; resolution happens from the post
text each time, so a journal replay re-runs the exact same routing.
"""

from typing import List, Optional

KJC_SHARED = 'kjc_shared'

# The sites a kjc_shared post can target, and the order used when a code is
# treated as valid for all of them.
KJC_SITES = ('rr88', 'mm88', 'xx88', 'gg88', 'llwin')


def kjc_sites_in_text(text: str) -> List[str]:
    """The KJC sites named in the post, in KJC_SITES order, de-duplicated."""
    if not text:
        return []
    upper = text.upper()
    return [site for site in KJC_SITES if site.upper() in upper]


def detect_kjc_site_from_text(text: str) -> Optional[str]:
    """The single KJC site a post targets, or None when it names several or none.

    None is not "no site" — for a kjc_shared post it means the code is shared
    across all five KJC sites (see resolve_sites).
    """
    named = kjc_sites_in_text(text)
    return named[0] if len(named) == 1 else None


def resolve_sites(site_id: Optional[str], text: str) -> List[str]:
    """Concrete site(s) a message's codes belong to.

    A normal site resolves to itself. A kjc_shared post resolves to the one site
    it names, or to all five KJC sites when it names several or none. An empty
    site resolves to no site (the caller then uses the generic filter).
    """
    site = (site_id or '').strip().lower()
    if site != KJC_SHARED:
        return [site] if site else []
    named = kjc_sites_in_text(text)
    if len(named) == 1:
        return named
    return list(KJC_SITES)
