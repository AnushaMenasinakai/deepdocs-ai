"""Deterministic page-bounded text processing, with no inference or OCR."""
import re
import pymupdf


class ProcessingFailure(ValueError):
    def __init__(self, message, status=422):
        self.status = status
        super().__init__(message)


# Parser diagnostics can include source paths/content. Never emit them.
pymupdf.TOOLS.mupdf_display_errors(False)
pymupdf.TOOLS.mupdf_display_warnings(False)


def normalize_text(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[^\S\n]+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def chunk_page(text, target, overlap):
    """Prefer paragraphs, then sentences, then whole words; never cross a page."""
    chunks = []
    start = 0
    while start < len(text):
        limit = min(start + target, len(text))
        end = limit
        if limit < len(text):
            lower = start + target // 2
            for pattern in (r"\n\n+", r"[.!?][\"')\]]*\s+", r"\s+"):
                candidates = [match.end() for match in re.finditer(pattern, text[start:limit])
                              if start + match.end() >= lower]
                if candidates:
                    end = start + candidates[-1]
                    break
            else:
                # A single long token may exceed the target; don't corrupt it.
                following = re.search(r"\s+", text[limit:])
                end = limit + following.end() if following else len(text)
            # Absorb a small tail only within a bounded extra allowance.
            if len(text) - end < target // 5 and len(text) - start <= target + overlap:
                end = len(text)
        value = text[start:end].strip()
        if value:
            chunks.append(value)
        if end == len(text):
            break
        next_start = end
        if overlap:
            suffix = max(start + 1, end - overlap)
            boundary = re.search(r"\s+", text[suffix:end])
            if boundary:
                next_start = suffix + boundary.end()
        start = max(start + 1, next_start)
    return chunks


def extract_chunks(path, settings):
    """Run synchronously: PyMuPDF is not used concurrently from worker threads."""
    # Close the OS handle before parsing: malformed PDFs can leave native
    # exception objects alive longer than expected on Windows.
    try:
        with path.open("rb") as source:
            data = source.read(settings.max_source_bytes + 1)
    except OSError:
        raise ProcessingFailure("Stored PDF is temporarily unavailable.", 503) from None
    if len(data) > settings.max_source_bytes:
        raise ProcessingFailure("The PDF exceeds the configured processing file limit.")
    pages = []
    total = 0
    try:
        with pymupdf.open(stream=data, filetype="pdf") as pdf:
            if not pdf.is_pdf or pdf.needs_pass:
                raise ProcessingFailure("The PDF is unreadable or password-protected.")
            if pdf.page_count > settings.max_pages:
                raise ProcessingFailure("The PDF exceeds the processing page limit.")
            for number, page in enumerate(pdf, start=1):
                raw = page.get_text("text", sort=True)
                total += len(raw)
                if total > settings.max_text_characters:
                    raise ProcessingFailure("The PDF exceeds the extracted text limit.")
                pages.append((number, raw))
    except ProcessingFailure:
        raise
    except Exception:
        raise ProcessingFailure("The PDF could not be read. It may be damaged.") from None

    chunks = []
    for page_number, raw in pages:
        clean = normalize_text(raw)
        for text in chunk_page(clean, settings.chunk_target, settings.chunk_overlap):
            chunks.append({
                "chunk_index": len(chunks), "text": text,
                "page_start": page_number, "page_end": page_number,
                "character_count": len(text),
            })
            if len(chunks) > settings.max_chunks:
                raise ProcessingFailure("The PDF exceeds the processing chunk limit.")
    if not chunks:
        raise ProcessingFailure("No extractable text was found. OCR is not supported.")
    return len(pages), chunks
