"""Phase 15A experiment only. No application imports this module."""
import re

VERSION = "1.0.0"
MAX_QUERIES = 4
STRATEGIES = ("original", "clauses", "topics", "combined")
QUESTION = r"(?:how|what|where|when|which|who|why|can|does|do|is|are|explain|describe|compare)\b"
ANAPHORA = re.compile(r"\b(?:it|its|they|their|them|this|that|these|those)\b", re.I)


def clauses(question):
    # Explicit separators or comma-and followed by a full question head only.
    # Anaphoric later clauses cannot be resolved deterministically: keep intact.
    parts = re.split(r";\s*|\?\s+(?=" + QUESTION + r")|,\s+and\s+(?=" + QUESTION + r")", question, flags=re.I)
    if len(parts) < 2 or any(not re.match(QUESTION, p.strip(), re.I) for p in parts):
        return []
    if any(ANAPHORA.search(p) for p in parts[1:]):
        return []
    return [p.strip().rstrip("?. ") for p in parts]


def topics(question):
    # Explicit imperative noun/topic lists. Never split a generic sentence on and.
    match = re.fullmatch(r"(?:Explain|Describe|Outline|List)\s+(.+?)[.?!]?", question, re.I)
    if match:
        body = match[1].rstrip(".?!")
        if not re.search(r"\band\b", body, re.I):
            return []
        parts = re.split(r",\s*(?:and\s+)?|\s+and\s+", body, flags=re.I)
        if len(parts) < 2 or any(not p.strip() for p in parts):
            return []
        # Single-word conjunctions (Research and development) are too ambiguous.
        if any(len(p.split()) < 2 or ANAPHORA.search(p) for p in parts):
            return []
        return [p.strip() for p in parts]
    # Narrow shared prepositional comparison; preserve the entire shared frame.
    # Does not expand acronyms, add synonyms, or guess omitted subjects.
    match = re.fullmatch(r"(Compare .+? in )([^,;?!]+?) and ([^,;?!]+?)[.?!]?", question, re.I)
    if match and not re.search(r"\band\b", match[2] + " " + match[3], re.I):
        return [(match[1] + part.rstrip(".?!")).strip() for part in (match[2], match[3])]
    return []


def decompose(question, strategy="combined"):
    if strategy not in STRATEGIES or not isinstance(question, str) or not question.strip() or len(question) > 1000:
        raise ValueError("Invalid evaluation query")
    original = question.strip()
    extras = []
    if strategy in ("clauses", "combined"):
        extras.extend(clauses(original))
    if strategy in ("topics", "combined"):
        extras.extend(topics(original))
    output, seen = [original], {original.casefold().rstrip("?. ")}
    for text in extras:
        key = text.casefold().rstrip("?. ")
        if key and key not in seen:
            output.append(text); seen.add(key)
        if len(output) == MAX_QUERIES:
            break
    return output
