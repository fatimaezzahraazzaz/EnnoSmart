from __future__ import annotations

import html
import re

_OUTER_FENCE_RE = re.compile(
    r"^```(?:markdown|md|text)?[ \t]*\n(?P<body>.*)\n```$",
    flags=re.I | re.S,
)


def normalize_markdown_text(value: str | None) -> str:
    text = html.unescape(str(value or ""))
    text = (
        text.replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\u00a0", " ")
        .replace("\u200b", "")
        .replace("\ufeff", "")
    )
    text = "\n".join(line.rstrip(" \t") for line in text.split("\n"))
    text = re.sub(r"(?m)^[ \t]+$", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_llm_markdown_output(value: str | None) -> str:
    text = str(value or "").strip()
    fenced = _OUTER_FENCE_RE.fullmatch(text)
    if fenced:
        text = fenced.group("body")
    return normalize_markdown_text(text)
