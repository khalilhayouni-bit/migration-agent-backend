"""RAG layer for Jira DC-to-Cloud migration agents."""

from rag.chroma_client import get_chroma_client, get_chroma_collection
from rag.embedder import GeminiEmbedder
from rag.retriever import Retriever

__all__ = [
    "get_chroma_client",
    "get_chroma_collection",
    "GeminiEmbedder",
    "Retriever",
    "get_retriever",
    "get_translation_memory",
]

_retriever: Retriever | None = None
_retriever_failed: bool = False


def get_retriever() -> Retriever | None:
    """Return a shared Retriever instance, or None if RAG is unavailable.

    Once a failure is detected (e.g. ChromaDB not installed, missing API key),
    subsequent calls short-circuit and return None immediately.
    """
    global _retriever, _retriever_failed
    if _retriever_failed:
        return None
    if _retriever is None:
        try:
            _retriever = Retriever()
        except Exception:
            _retriever_failed = True
            return None
    return _retriever


_translation_memory = None


def get_translation_memory():
    """Return a shared TranslationMemory instance, or None if unavailable.

    Lazily imported and instantiated once. Retries on every call until
    successful — does not permanently poison on a transient failure.
    """
    global _translation_memory
    if _translation_memory is None:
        try:
            from rag.translation_memory import TranslationMemory
            _translation_memory = TranslationMemory()
        except Exception as e:
            import traceback
            print(f"[Cache] TranslationMemory init FAILED: {type(e).__name__}: {e}")
            traceback.print_exc()
            return None
    return _translation_memory
