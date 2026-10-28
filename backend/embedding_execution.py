"""Worker-side embedding entry points; no model or executor is allocated here."""
import embeddings


def query_vector(text, settings):
    dimension = embeddings.model_dimension(embeddings.load_model(settings.model_name))
    vector = embeddings.validate_vector(embeddings.embed_text(text, settings), dimension)
    return dimension, vector


class Batches:
    """Create and advance the generator only through session.run(next_batch)."""
    def __init__(self, texts, settings, factory=embeddings.embed_batches):
        self._texts = texts
        self._settings = settings
        self._factory = factory
        self._iterator = None

    def next_batch(self):
        if self._iterator is None:
            self._iterator = iter(self._factory(self._texts, self._settings))
        return next(self._iterator, None)
