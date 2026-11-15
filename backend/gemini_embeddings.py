"""Opt-in synchronous SDK adapter, called only inside the owned embedding worker."""
import asyncio
import math
import time
from config import load_gemini_settings
from embeddings import EmbeddingFailure, validate_vector

REQUEST_SECONDS = 30
MAX_BATCH = 16
# Conservative byte budget below the 2,048-token model limit; no local tokenizer
# or extra counting request. Intentionally rejects some otherwise valid long text.
MAX_INPUT_BYTES = 1024


def embed_remote(texts, settings, task_type):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise EmbeddingFailure("Remote embeddings require an owned worker.")
    if (settings.provider != "gemini" or settings.gemini_model != "gemini-embedding-001"
            or settings.dimension != 768 or task_type not in ("RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY")):
        raise EmbeddingFailure("Invalid remote embedding configuration.")
    try:
        if not 1 <= len(texts) <= MAX_BATCH or any(
                not isinstance(t, str) or not t.strip() or len(t.encode('utf-8')) > MAX_INPUT_BYTES for t in texts):
            raise ValueError
    except (ValueError, TypeError, UnicodeError):
        raise EmbeddingFailure("Embedding input exceeds the remote input budget.", 409) from None
    try:
        from google import genai
        from google.genai import types
        deadline = time.monotonic() + REQUEST_SECONDS
        key = load_gemini_settings().api_key
        # Each worker request owns and closes its client; no event-loop client reuse,
        # background retry, local fallback, or stream escaping worker ownership.
        with genai.Client(api_key=key, vertexai=False,
                http_options=types.HttpOptions(timeout=REQUEST_SECONDS * 1000,
                    retry_options=types.HttpRetryOptions(attempts=1))) as client:
            response = client.models.embed_content(model=settings.gemini_model, contents=texts,
                config=types.EmbedContentConfig(task_type=task_type,
                                              output_dimensionality=settings.dimension))
        if time.monotonic() >= deadline:
            raise TimeoutError
        if response.embeddings is None or len(response.embeddings) != len(texts):
            raise ValueError
        output = []
        for item in response.embeddings:
            vector = validate_vector(item.values, settings.dimension)
            norm = math.hypot(*vector)
            if not math.isfinite(norm) or norm <= 0:
                raise ValueError
            output.append(validate_vector([v / norm for v in vector], settings.dimension))
        return output
    except Exception:
        raise EmbeddingFailure() from None
