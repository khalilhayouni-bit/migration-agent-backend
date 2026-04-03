#!/usr/bin/env python3
"""Index local documentation files into ChromaDB for RAG retrieval.

Usage:
    python scripts/index_docs.py ./docs
    python scripts/index_docs.py ./docs --chunk-size 400 --overlap 50

Accepts .md, .txt, and .html files. Idempotent — re-running upserts
without creating duplicates (document IDs are deterministic hashes).
"""

import argparse
import hashlib
import sys
from html.parser import HTMLParser
from pathlib import Path

# Add project root to path so rag/ and app/ are importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag.chroma_client import get_chroma_collection
from rag.embedder import GeminiEmbedder

SUPPORTED_EXTENSIONS = {".md", ".txt", ".html", ".htm"}

# Approximate token-to-character ratio for English text
CHARS_PER_TOKEN = 4


# ---------------------------------------------------------------------------
# HTML stripping
# ---------------------------------------------------------------------------

class _HTMLStripper(HTMLParser):
    """Minimal HTML tag stripper."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)


def strip_html(html: str) -> str:
    """Remove HTML tags and return plain text."""
    stripper = _HTMLStripper()
    stripper.feed(html)
    return stripper.get_text()


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def _extract_heading(text: str) -> str | None:
    """Find the last markdown heading in a block of text."""
    for line in reversed(text.split("\n")):
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return None


def chunk_text(
    text: str,
    chunk_size_tokens: int = 400,
    overlap_tokens: int = 50,
) -> list[dict[str, str]]:
    """Split text into chunks of approximately chunk_size_tokens with overlap.

    Returns a list of dicts with keys: text, section.
    """
    chunk_chars = chunk_size_tokens * CHARS_PER_TOKEN
    overlap_chars = overlap_tokens * CHARS_PER_TOKEN
    step = max(chunk_chars - overlap_chars, 1)

    chunks: list[dict[str, str]] = []
    start = 0

    while start < len(text):
        end = start + chunk_chars
        segment = text[start:end].strip()

        if not segment:
            start += step
            continue

        # Look backwards from the current position to find the nearest heading
        prefix = text[:end]
        section = _extract_heading(prefix)

        chunks.append({"text": segment, "section": section or ""})
        start += step

    return chunks


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------

def _doc_id(source: str, chunk_index: int, text: str) -> str:
    """Generate a deterministic document ID for idempotent upserts."""
    payload = f"{source}::chunk_{chunk_index}::{text[:200]}"
    return hashlib.sha256(payload.encode()).hexdigest()[:20]


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------

def index_folder(folder: Path, chunk_size: int = 400, overlap: int = 50) -> None:
    """Walk a folder, chunk all supported files, embed, and upsert into ChromaDB."""
    collection = get_chroma_collection()
    embedder = GeminiEmbedder()

    files = sorted(
        f for f in folder.rglob("*")
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    )

    if not files:
        print(f"No supported files (.md, .txt, .html) found in {folder}")
        return

    print(f"Found {len(files)} file(s) to index.\n")

    total_chunks = 0

    for file_path in files:
        rel_path = file_path.relative_to(folder)
        print(f"  {rel_path} ... ", end="", flush=True)

        raw = file_path.read_text(encoding="utf-8", errors="replace")

        if file_path.suffix.lower() in {".html", ".htm"}:
            raw = strip_html(raw)

        chunks = chunk_text(raw, chunk_size_tokens=chunk_size, overlap_tokens=overlap)

        if not chunks:
            print("(empty, skipped)")
            continue

        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, str]] = []

        source_name = str(rel_path)

        for i, chunk in enumerate(chunks):
            ids.append(_doc_id(source_name, i, chunk["text"]))
            documents.append(chunk["text"])
            metadatas.append({
                "source": source_name,
                "section": chunk["section"],
            })

        embeddings = embedder.embed_batch(documents)

        collection.upsert(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
        )

        total_chunks += len(chunks)
        print(f"{len(chunks)} chunk(s)")

    print(f"\nDone. {total_chunks} chunks indexed this run "
          f"({collection.count()} total documents in collection).")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Index documentation files into ChromaDB for RAG retrieval.",
    )
    parser.add_argument(
        "folder",
        type=Path,
        help="Path to folder containing .md, .txt, or .html documentation files.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=400,
        help="Chunk size in approximate tokens (default: 400).",
    )
    parser.add_argument(
        "--overlap",
        type=int,
        default=50,
        help="Chunk overlap in approximate tokens (default: 50).",
    )

    args = parser.parse_args()

    if not args.folder.is_dir():
        print(f"Error: '{args.folder}' is not a directory.")
        sys.exit(1)

    index_folder(args.folder, args.chunk_size, args.overlap)


if __name__ == "__main__":
    main()
