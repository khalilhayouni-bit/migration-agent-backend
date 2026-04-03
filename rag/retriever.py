"""RAG retriever that queries ChromaDB for relevant documentation chunks."""

from rag.chroma_client import get_chroma_collection
from rag.embedder import GeminiEmbedder


class Retriever:
    """Queries the jira_migration_docs ChromaDB collection for relevant context."""

    def __init__(self) -> None:
        self._collection = get_chroma_collection()
        self._embedder = GeminiEmbedder()

    def query(self, text: str, n_results: int = 5) -> list[dict]:
        """Retrieve the most relevant documentation chunks for the given text.

        Args:
            text: The query text (typically the source script or a summary of it).
            n_results: Maximum number of chunks to return.

        Returns:
            List of dicts, each with keys: content, source, score.
            Score is a cosine similarity in [0, 1] (higher is better).
        """
        if not text or not text.strip():
            return []

        collection_size = self._collection.count()
        if collection_size == 0:
            return []

        embedding = self._embedder.embed(text)
        results = self._collection.query(
            query_embeddings=[embedding],
            n_results=min(n_results, collection_size),
            include=["documents", "metadatas", "distances"],
        )

        chunks: list[dict] = []
        for i in range(len(results["ids"][0])):
            meta = results["metadatas"][0][i] or {}
            source_parts = [meta.get("source", "unknown")]
            if meta.get("section"):
                source_parts.append(meta["section"])

            chunks.append({
                "content": results["documents"][0][i],
                "source": " > ".join(source_parts),
                "score": round(1.0 - results["distances"][0][i], 4),
            })

        return chunks
