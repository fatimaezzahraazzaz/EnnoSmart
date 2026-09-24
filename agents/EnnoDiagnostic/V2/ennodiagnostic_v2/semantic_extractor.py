import hashlib
import json
import re
import unicodedata
from typing import Any, Callable, Dict, Iterable, List, Protocol, Union
from .prompts import SEMANTIC_EXTRACTOR_SYSTEM, build_extractor_user_prompt
from .schemas import Chunk, EvidenceRef, ExtractionItem

class LLMClientProtocol(Protocol):
    def extract_json(self, system_prompt: str, user_prompt: str) -> Union[str, Dict[str, Any]]:
        ...

class CallableLLMAdapter:
    def __init__(self, fn: Callable[[str, str], Union[str, Dict[str, Any]]]):
        self.fn = fn
    def extract_json(self, system_prompt: str, user_prompt: str):
        return self.fn(system_prompt, user_prompt)

def parse_json_payload(payload):
    if isinstance(payload, dict):
        return payload
    if not isinstance(payload, str):
        raise TypeError("Le client LLM doit retourner dict ou str JSON.")
    text = re.sub(r"^```(?:json)?\s*", "", payload.strip(), flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)

def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text or ""))
    return re.sub(r"\s+", " ", text).strip().lower()

def quote_is_in_source(quote: str, source: str) -> bool:
    q, s = _norm(quote), _norm(source)
    return bool(q) and q in s

def _id(prefix: str, *parts: str) -> str:
    d = hashlib.sha1("|".join(parts).encode()).hexdigest()[:14]
    return f"{prefix}_{d}"

KEY_TO_KIND = {
    "objectives": "objective",
    "locks": "lock",
    "methods": "method",
    "parameters": "parameter",
    "results": "result",
}

class SemanticExtractor:
    def __init__(self, llm: LLMClientProtocol):
        self.llm = llm

    def extract_chunk(self, chunk: Chunk) -> List[ExtractionItem]:
        prompt = build_extractor_user_prompt(
            chunk.document_name,
            chunk.document_mode,
            chunk.section_title,
            chunk.context_before,
            chunk.text,
            chunk.context_after,
        )
        data = parse_json_payload(
            self.llm.extract_json(SEMANTIC_EXTRACTOR_SYSTEM, prompt)
        )
        out = []
        for key, kind in KEY_TO_KIND.items():
            values = data.get(key) or []
            if not isinstance(values, list):
                continue
            for i, value in enumerate(values):
                if not isinstance(value, dict):
                    continue
                statement = str(value.get("statement") or "").strip()
                quote = str(value.get("evidence_quote") or "").strip()
                if not statement or not quote:
                    continue

                verified = quote_is_in_source(quote, chunk.text)
                evidence = EvidenceRef(
                    evidence_id=_id("ev", chunk.chunk_id, quote),
                    document_id=chunk.document_id,
                    document_name=chunk.document_name,
                    chunk_id=chunk.chunk_id,
                    quote=quote,
                    section_title=chunk.section_title,
                    page=chunk.metadata.get("page"),
                    quote_verified=verified,
                )

                conf = value.get("confidence")
                try:
                    conf = float(conf) if conf is not None else None
                except (TypeError, ValueError):
                    conf = None

                explicitness = str(value.get("explicitness") or "unknown").lower()
                if explicitness not in {"explicit", "implicit", "unknown"}:
                    explicitness = "unknown"

                out.append(ExtractionItem(
                    item_id=_id("it", chunk.chunk_id, key, str(i), statement),
                    kind=kind,
                    statement=statement,
                    evidence=evidence,
                    explicitness=explicitness,
                    rationale=str(value.get("rationale") or "").strip(),
                    technical_object=str(value.get("technical_object") or "").strip(),
                    unresolved_question=str(value.get("unresolved_question") or "").strip(),
                    llm_confidence=conf,
                    needs_review=not verified,
                    metadata={"source": "semantic_llm", "raw_category": key},
                ))
        return out

    def extract(self, chunks: Iterable[Chunk]) -> List[ExtractionItem]:
        out = []
        for chunk in chunks:
            out.extend(self.extract_chunk(chunk))
        return out
