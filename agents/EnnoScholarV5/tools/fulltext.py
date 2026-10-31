"""Same recovery code; carry headings from cached pages into continuation batches."""
from ..reuse import load
from .extraction import previous_chunks
base = load("tools._fulltext_base", "tools/fulltext.py")
for name in dir(base):
    if not name.startswith("_"):
        globals()[name] = getattr(base, name)
_original_get_fulltext = get_fulltext

def get_fulltext(article, *args, **kwargs):
    from ..html_currency import mark
    token = previous_chunks.set(article.fulltext.get("chunks", ()))
    try:
        # Only V5's html_document (imported by the base module above) produces HTML text here.
        return mark(_original_get_fulltext(article, *args, **kwargs))
    finally:
        previous_chunks.reset(token)
