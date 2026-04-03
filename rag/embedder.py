"""Embedding wrapper using Google Gemini text-embedding-004.

All embeddings in the RAG layer go through this module to ensure a single
embedding model is used for both indexing and retrieval.
"""

from google import genai
from app.config import settings

_client: genai.Client | None = None

EMBEDDING_MODEL = "models/gemini-embedding-001"


def _get_client() -> genai.Client:
    """Return a cached genai client instance."""
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


class GeminiEmbedder:
    """Generates vector embeddings via Gemini text-embedding-004."""

    def __init__(self) -> None:
        self._client = _get_client()

    def embed(self, text: str) -> list[float]:
        """Embed a single text string and return its vector."""
        result = self._client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=text,
        )
        return result.embeddings[0].values

    def embed_batch(self, texts: list[str], batch_size: int = 50) -> list[list[float]]:
        """Embed multiple texts in batches, returning vectors in input order."""
        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            result = self._client.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=batch,
            )
            all_embeddings.extend(emb.values for emb in result.embeddings)
        return all_embeddings
