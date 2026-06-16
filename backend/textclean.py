"""Read-aloud text cleanup: condense in-text author-year citations and strip
parenthetical figure/table asides so academic prose listens cleanly.

Conservative by design: a parenthetical that does not clearly match a rule is
left untouched (prefer under-cleaning over mangling a sentence).
"""

import re

_YEAR = r"(?:1[89]\d\d|20\d\d)[a-z]?"
_YEAR_RE = re.compile(_YEAR)

# Lead-in words that precede a citation and should be dropped from the surname.
_LEADIN_RE = re.compile(
    r"^(?:see(?:\s+also)?|e\.g\.?,?|cf\.?,?|for\s+(?:a\s+)?review|viz\.?,?)\s+",
    re.I,
)

# Parenthetical whose content is ONLY years plus page refs / commas / connectors.
_BARE_YEAR_CONTENT_RE = re.compile(
    r"^(?:" + _YEAR + r"|pp?\.|n\.d\.|in\s+press|forthcoming|and|&|[,;]|\s|\d+)+$",
    re.I,
)

# Parenthetical figure/table aside: optional lead-ins, then Fig/Figure/Table/Tbl + number.
_FIG_TABLE_CONTENT_RE = re.compile(
    r"^(?:see|cf\.?|e\.g\.?,?|also|and|[,;]|\s)*"
    r"(?:figs?\.?|figures?|t(?:able|bl)s?\.?)\s*\d.*$",
    re.I,
)

_PAREN_RE = re.compile(r"\(([^()]*)\)")
_ETAL_RE = re.compile(r"\bet\s+al\.?.*$", re.I)


def simplify_citations(text: str) -> str:
    """Condense author-year citations and remove parenthetical fig/table asides."""
    def repl(match):
        inner = match.group(1).strip()
        if not inner:
            return match.group(0)
        if _FIG_TABLE_CONTENT_RE.match(inner):
            return ""
        if _YEAR_RE.search(inner) and _BARE_YEAR_CONTENT_RE.match(inner):
            return ""
        condensed = _condense_group(inner)
        if condensed is not None:
            return "(" + condensed + ")"
        return match.group(0)

    return _cleanup(_PAREN_RE.sub(repl, text))


def _condense_group(inner: str) -> str | None:
    """Condense a ';'-separated citation group, or None if any part isn't a citation."""
    parts = []
    for raw in inner.split(";"):
        one = _condense_one(raw.strip())
        if one is None:
            return None
        parts.append(one)
    return "; ".join(parts) if parts else None


def _condense_one(part: str) -> str | None:
    """'Smith, Jones & Lee, 2020' -> 'Smith and colleagues'; or None if not a citation."""
    year = _YEAR_RE.search(part)
    if not year:
        return None
    authors = _LEADIN_RE.sub("", part[: year.start()]).strip().rstrip(",").strip()
    if not authors:
        return None
    multi = bool(
        re.search(r"\bet\s+al", authors, re.I)
        or "&" in authors
        or re.search(r"\band\b", authors, re.I)
        or "," in authors
    )
    first = _ETAL_RE.sub("", re.split(r",|&|\band\b", authors)[0]).strip()
    if not re.search(r"[A-ZÀ-Þ]", first):  # needs a capitalised surname
        return None
    return f"{first} and colleagues" if multi else first


def _cleanup(text: str) -> str:
    # Drop the space before stray punctuation left by a removed parenthetical.
    # The (?!\d) guard avoids welding a decimal back together, e.g. a stat like
    # "p < .05" must not become "p <.05".
    text = re.sub(r"\s+([.,;:!?])(?!\d)", r"\1", text)
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()
