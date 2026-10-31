"""Existing provenance checks with the same narrow chemical title equivalence."""
from .reuse import load
base = load("_provenance_base", "provenance.py")

def bibliographic_match(article, title="", authors=(), front=""):
    from .sources import title_key
    from .models import public_link
    from .versions import tokens
    wanted = title_key(article.title)
    actual = title_key(title) if title and not public_link(title) else ""
    title_matches = bool(wanted and (actual == wanted or not actual and wanted in title_key(front[:1200])))
    known = {tuple(tokens(a)) for a in article.authors if len(tokens(a)) >= 2}
    found = {tuple(tokens(a)) for a in authors if len(tokens(a)) >= 2}
    words = " ".join(tokens(front))
    return bool(title_matches and known and (known & found or any(" ".join(a) in words for a in known)))

base.bibliographic_match = bibliographic_match
for name in dir(base):
    if not name.startswith("_"):
        globals()[name] = getattr(base, name)
