"""Offline estimates, not a billing guarantee. Never downloads a tokenizer.

A locally cached o200k tokenizer is used only after checking its complete cache.
Otherwise a text/JSON/formula-aware point estimate is reported separately from
the conservative admission envelope. Actual supplier usage always settles it.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from functools import lru_cache

ENCODING_URL = 'https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken'


@lru_cache(maxsize=1)
def local_tokenizer():
    cache = Path(os.getenv('TIKTOKEN_CACHE_DIR') or os.getenv('DATA_GYM_CACHE_DIR') or Path(tempfile.gettempdir())/'data-gym-cache')
    path = cache/hashlib.sha1(ENCODING_URL.encode()).hexdigest()
    if not path.is_file():
        return None
    # tiktoken would fetch on a corrupt cache, so verify BEFORE calling it.
    if hashlib.sha256(path.read_bytes()).hexdigest() != '446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d':
        return None
    try:
        import tiktoken
        return tiktoken.get_encoding('o200k_base')
    except (ImportError, ValueError):
        return None


def estimate(request):
    blocks = {'instructions': request.get('instructions') or '',
        'tools':json.dumps(request.get('tools', []),ensure_ascii=False),
        'input':json.dumps(request.get('input', []),ensure_ascii=False)}
    tokenizer = local_tokenizer()
    def count(value):
        if tokenizer:
            return len(tokenizer.encode(value,disallowed_special=()))
        # Words, numbers, formula operators and JSON punctuation do not have the
        # same density. This is a point estimate, deliberately not an upper bound.
        units = re.findall(r'[^\W\d_]+|\d{1,3}|[^\w\s]',value,re.UNICODE)
        return sum(max(1,math.ceil(len(unit.encode('utf8'))/4)) if unit.isalpha() else 1 for unit in units)
    counts = {k:count(v) for k,v in blocks.items()}
    sizes = {k:len(v.encode('utf8')) for k,v in blocks.items()}
    point = sum(counts.values())+32+8*len(request.get('input', []))
    # Keep the pre-existing byte envelope as a floor when the model tokenizer is
    # unattested. Known o200k counts still need framing/model-compatibility margin.
    if tokenizer:
        bound = math.ceil(point*1.15)+256
    else:
        # The lexical approximation can overcount dense JSON punctuation. Do not
        # compound that error with another margin on top of the existing 3-byte
        # envelope (it otherwise blocks even the first call at the .50 USD trial).
        bound = math.ceil(sum(sizes.values())/3)
        point = min(point,bound)
    return {'method':'local_o200k_with_margin' if tokenizer else 'offline_lexical_with_byte_envelope',
        'model_tokenizer_attested':False, 'point_input_tokens':point, 'admission_input_tokens':bound,
        'block_bytes':sizes, 'block_tokens_estimated':counts, 'max_output_tokens':request.get('max_output_tokens'),
        'billing_guarantee':False}
