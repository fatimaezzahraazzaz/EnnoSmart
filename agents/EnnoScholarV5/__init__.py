"""Experimental V5: reuse V4 sources under an isolated Python namespace.

Relative imports resolve V5 overrides first; unchanged scientific modules execute
from their existing V4 files. No monkey patch affects agents.EnnoScholarV4.
"""
from pathlib import Path
__path__.append(str(Path(__file__).resolve().parent.parent / "EnnoScholarV4"))
