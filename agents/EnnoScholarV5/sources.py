"""Only chemical typography is normalised; DOI and title conflicts remain errors."""
import re
from .reuse import load
base = load("_sources_base", "sources.py")
_original_title_key = base.title_key

def title_key(value):
    # Narrow equivalence required by the diagnostic: CO 2 / CO₂ / CO<sub>2</sub>.
    text = re.sub(r"\bCO\s+2\b", "CO2", base.title_text(value), flags=re.I)
    # Inline JATS/HTML typography can leave a space after an index: Li3 PO4.
    # Join only complete formula tokens containing a number, never title words.
    formula = r'\b(?:[A-Z][a-z]?\d*(?:\s*[A-Z][a-z]?\d*)+)(?:\s*[+−-])?(?!\w)'
    text = re.sub(formula, lambda m: re.sub(r'\s+', '', m[0]) if re.search(r'\d',m[0]) else m[0], text)
    text = re.sub(r'\b((?:[A-Z][a-z]?\d*)+)\s*-(?=\s|$|[),;])',r'\1−',text)
    return _original_title_key(text)

base.title_key = title_key
for name in dir(base):
    if not name.startswith("_"):
        globals()[name] = getattr(base, name)
