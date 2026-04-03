"""Singleton ChromaDB client for the RAG layer.

Provides a single persistent ChromaDB instance shared across all agents,
stored in ./chroma_db/ relative to the project root.
"""

import chromadb
from pathlib import Path

_client: chromadb.ClientAPI | None = None
_collection: chromadb.Collection | None = None

CHROMA_DB_PATH = str(Path(__file__).resolve().parent.parent / "chroma_db")
COLLECTION_NAME = "jira_migration_docs"


def get_chroma_client() -> chromadb.ClientAPI:
    """Return the singleton ChromaDB persistent client."""
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
    return _client


def get_chroma_collection() -> chromadb.Collection:
    """Return the jira_migration_docs collection, creating it if needed."""
    global _collection
    if _collection is None:
        client = get_chroma_client()
        _collection = client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection
