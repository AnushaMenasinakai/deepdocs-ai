"""Lazy CPU embedding provider; vectors exist only in the caller's memory."""
import math
from functools import lru_cache
from numbers import Real, Integral
from pathlib import Path
from threading import RLock

_CACHE = Path(__file__).resolve().parent / ".cache" / "embeddings"
_LOCK = RLock()


class EmbeddingFailure(ValueError):
    def __init__(self, message="Embedding generation is temporarily unavailable.", status=503):
        super().__init__(message)
        self.status = status


@lru_cache(maxsize=1)
def _load_model(name):
    # Importing the application never imports torch or loads/downloads weights.
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(name, device="cpu", cache_folder=str(_CACHE), trust_remote_code=False)


def load_model(name):
    try:
        with _LOCK:
            return _load_model(name)
    except Exception:
        raise EmbeddingFailure("Embedding model is temporarily unavailable.") from None


def model_dimension(model):
    try:
        value = model.get_sentence_embedding_dimension()
        if isinstance(value, bool) or not isinstance(value, Integral) or value < 1:
            raise ValueError
        return int(value)
    except Exception:
        raise EmbeddingFailure("Embedding model dimension is invalid.") from None


def validate_vector(vector, dimension):
    try:
        values = list(vector)
        if len(values) != dimension or not values:
            raise ValueError
        if any(isinstance(value, bool) or not isinstance(value, Real) or
               not math.isfinite(value) for value in values):
            raise ValueError
        return [float(value) for value in values]
    except Exception:
        raise EmbeddingFailure("Embedding vector validation failed.") from None


def embed_batches(texts, settings):
    """Yield validated batches; the consumer can discard each before the next."""
    if not texts or any(not isinstance(text, str) or not text.strip() for text in texts):
        raise EmbeddingFailure("Embedding input must contain non-empty text.", 409)
    model = load_model(settings.model_name)
    dimension = model_dimension(model)
    for offset in range(0, len(texts), settings.batch_size):
        batch = texts[offset:offset + settings.batch_size]
        try:
            with _LOCK:
                vectors = model.encode(batch, batch_size=settings.batch_size,
                                       show_progress_bar=False, convert_to_numpy=True,
                                       normalize_embeddings=True)
            if len(vectors) != len(batch):
                raise ValueError
        except Exception:
            raise EmbeddingFailure() from None
        yield dimension, [validate_vector(vector, dimension) for vector in vectors]


def embed_text(text, settings):
    return next(embed_batches([text], settings))[1][0]
