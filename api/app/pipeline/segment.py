"""Component 4: split SIIS into sections and numbered sentences; rank sections per intent.

Rule (data/fixtures/README.md): drop everything before the first `#`, split on `#` headers, split
each remaining line into sentences on `[.!?]` followed by whitespace and a capital, and number the
sentences `S1..Sn` across the whole article. Steps cite these ids, so the rule must stay stable.
"""

import re
from collections import OrderedDict

import numpy as np

from app.config import settings
from app.models import Intent, SiisSentence
from app.retrieval import dense

_HEADER = re.compile(r"^(#{1,6})\s*(.*?)\s*#*\s*$")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def split_sections(siis_clean: str) -> list[dict]:
    """[{id, heading, level, sentences: [text]}] in article order. Text before the first header is
    the listing's breadcrumb, not the article, and is dropped."""
    start = siis_clean.find("#")
    if start < 0:
        body, sections = siis_clean, [{"id": "sec1", "heading": "", "level": 1, "sentences": []}]
    else:
        body, sections = siis_clean[start:], []
    for line in body.split("\n"):
        line = line.strip()
        if not line:
            continue
        header = _HEADER.match(line)
        if header:
            sections.append(
                {
                    "id": f"sec{len(sections) + 1}",
                    "heading": header.group(2),
                    "level": len(header.group(1)),
                    "sentences": [],
                }
            )
            continue
        sections[-1]["sentences"].extend(s.strip() for s in _SENTENCE_BREAK.split(line) if s.strip())
    return [s for s in sections if s["sentences"] or s["heading"]]


def _section_text(section: dict) -> str:
    """Heading plus the opening of the body: what the section is about, and cheap to embed."""
    body = " ".join(section["sentences"])[: settings.section_embed_chars]
    return f"{section['heading']}. {body}".strip(". ")


# Section vectors by text. A request embeds its article twice (segment, then the rescore once call B
# has named the intents); an 18k-character article has 65 sections, ~1.5 s of CPU each time.
_section_vectors: OrderedDict[str, list[float]] = OrderedDict()
_SECTION_VECTORS_MAX = 4096


def _embed_sections(texts: list[str]) -> list[list[float]]:
    missing = list(dict.fromkeys(t for t in texts if t not in _section_vectors))
    for text, vector in zip(missing, dense.embed(missing)):
        _section_vectors[text] = vector
    for text in texts:
        _section_vectors.move_to_end(text)
    while len(_section_vectors) > _SECTION_VECTORS_MAX:
        _section_vectors.popitem(last=False)
    return [_section_vectors[t] for t in texts]


def prewarm_kit() -> int:
    """Embed the kit articles' sections at startup. The first requests on a fresh process spent 1.4-1.8 s
    in segment (the embedder's first long inputs) against ~20 ms once warm; this moves that cost into
    startup, before /health goes green, and fills the section cache for the kit articles."""
    import json
    from pathlib import Path

    from app.pipeline.normalize import clean_siis

    path = Path(settings.data_dir) / "kit" / "siis_responses.json"
    if not path.exists():
        return 0
    texts = []
    for record in json.loads(path.read_text(encoding="utf-8")).get("responses", []):
        clean, _ = clean_siis(record.get("siis_response"))
        texts += [_section_text(s) for s in split_sections(clean)] if clean else []
    return len(_embed_sections(texts))


def section_relevance(sections: list[dict], intents: list[Intent]) -> list[list[float]]:
    """Cosine between each intent and each section (heading + body): [section][intent], 0-1."""
    if not sections or not intents:
        return [[0.0] * len(intents) for _ in sections]
    section_vectors = np.asarray(_embed_sections([_section_text(s) for s in sections]), dtype=np.float32)
    intent_vectors = np.asarray(dense.embed([i.text for i in intents]), dtype=np.float32)
    sims = section_vectors @ intent_vectors.T
    return [[round(float(min(max(v, 0.0), 1.0)), 2) for v in row] for row in sims]


def segment(siis_clean: str, intents: list[Intent]) -> list[SiisSentence]:
    """Numbered sentences; each carries its section's best relevance over the intents."""
    sentences, _ = segment_with_sections(siis_clean, intents)
    return sentences


def segment_with_sections(siis_clean: str, intents: list[Intent]) -> tuple[list[SiisSentence], list[dict]]:
    """Sentences plus the section table the stream's `segment` event and the compiler use.

    Section rows: {id, heading, level, sentence_ids, relevance: [per intent], relevant}.
    """
    sections = split_sections(siis_clean)
    relevance = section_relevance(sections, intents)
    sentences: list[SiisSentence] = []
    table: list[dict] = []
    for section, scores in zip(sections, relevance):
        best = max(scores, default=0.0)
        ids = []
        for text in section["sentences"]:
            sid = f"S{len(sentences) + 1}"
            ids.append(sid)
            sentences.append(SiisSentence(id=sid, section=section["heading"], text=text, relevance=best))
        table.append(
            {
                "id": section["id"],
                "heading": section["heading"],
                "level": section["level"],
                "sentence_ids": ids,
                "relevance": scores,
                "relevant": best >= settings.section_relevance_floor,
            }
        )
    return sentences, table
