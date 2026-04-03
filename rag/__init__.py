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
_translation_memory_failed: bool = False


def get_translation_memory():
    """Return a shared TranslationMemory instance, or None if unavailable.

    Lazily imported and instantiated once. Any failure short-circuits
    subsequent calls to return None immediately.
    """
    global _translation_memory, _translation_memory_failed
    if _translation_memory_failed:
        return None
    if _translation_memory is None:
        try:
            from rag.translation_memory import TranslationMemory
            _translation_memory = TranslationMemory()
        except Exception:
            _translation_memory_failed = True
            return None
    return _translation_memory
