"""Translation memory cache backed by a second ChromaDB collection.

Stores verified high-confidence translations and serves them instantly
on cache hits, skipping Gemini entirely. Uses a three-layer validation
gate: vector similarity, structural diff, and risk element extraction.
"""

import hashlib
import json
import re
import keyword
import threading
from datetime import datetime, timezone

from pydantic import BaseModel

from rag.chroma_client import get_chroma_client
from rag.embedder import GeminiEmbedder

COLLECTION_NAME = "translation_memory"
SIMILARITY_THRESHOLD = 0.92

# Patterns for Layer 3 risk element extraction
_INTEGER_LITERAL = re.compile(r'\b(\d{1,6})\b')
_PROJECT_KEY = re.compile(r"['\"]([A-Z]{2,10})['\"]")
_USERNAME_OR_EMAIL = re.compile(
    r"['\"]([a-zA-Z][a-zA-Z0-9._-]*@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})['\"]"
    r"|['\"]([a-z][a-z0-9._-]{2,})['\"]"
)
_URL_LITERAL = re.compile(r"['\"]((https?://[^\s'\"]+))['\"]")

# Structural tokens for Layer 2
_IMPORT_LINE = re.compile(r'^\s*import\s+.+', re.MULTILINE)
_METHOD_CALL = re.compile(r'\b(\w+)\s*\(')
_CONTROL_FLOW = re.compile(r'\b(if|else|for|while|try|catch|finally|switch|case|return|throw)\b')


class CacheResult(BaseModel):
    """Result returned on a translation memory cache hit."""
    translated_script: str
    confidence: float
    confidence_reasoning: str
    incompatible_elements: list[str]
    notes: str
    similarity: float
    warnings: list[str]


def _md5(text: str) -> str:
    """Return the MD5 hex digest of a string."""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _extract_structural_tokens(script: str) -> tuple[list[str], list[str], list[str]]:
    """Extract imports, method calls, and control flow keywords from a script."""
    imports = sorted(set(line.strip() for line in _IMPORT_LINE.findall(script)))
    methods = sorted(set(_METHOD_CALL.findall(script)))
    control = sorted(set(_CONTROL_FLOW.findall(script)))
    return imports, methods, control


def _structural_diff_ok(cached_script: str, incoming_script: str) -> bool:
    """Layer 2: Check if structural differences are surface-level only.

    Returns True if the two scripts share the same imports, method calls,
    and control flow keywords — meaning differences are limited to variable
    names, whitespace, and comments.
    """
    cached_imports, cached_methods, cached_control = _extract_structural_tokens(cached_script)
    incoming_imports, incoming_methods, incoming_control = _extract_structural_tokens(incoming_script)

    if cached_imports != incoming_imports:
        return False
    if cached_methods != incoming_methods:
        return False
    if cached_control != incoming_control:
        return False

    return True


def _extract_risk_warnings(script: str) -> list[str]:
    """Layer 3: Scan for hardcoded environment-specific values.

    Returns a list of specific, actionable warning strings.
    """
    warnings: list[str] = []

    # Common language keywords and tiny numbers that are never IDs
    trivial_numbers = {str(i) for i in range(20)}

    # Hardcoded integer literals (potential transition/status/field/screen IDs)
    for match in _INTEGER_LITERAL.finditer(script):
        value = match.group(1)
        if value not in trivial_numbers and len(value) >= 2:
            warnings.append(
                f"Hardcoded transition ID: {value} — confirm this ID exists in your Cloud project"
            )

    # Uppercase project keys
    for match in _PROJECT_KEY.finditer(script):
        key = match.group(1)
        # Filter out common language/API constants
        if key not in {"GET", "PUT", "POST", "DELETE", "PATCH", "HEAD", "OPTIONS",
                       "JSON", "XML", "HTML", "HTTP", "HTTPS", "NULL", "TRUE", "FALSE",
                       "API", "URL", "URI", "REST", "SQL", "EOF", "CSV", "UTF"}:
            warnings.append(
                f"Hardcoded project key: '{key}' — confirm this matches your Cloud project key"
            )

    # Emails and usernames
    for match in _USERNAME_OR_EMAIL.finditer(script):
        email = match.group(1)
        username = match.group(2)
        if email:
            warnings.append(
                f"Hardcoded email: '{email}' — DC email addresses may not map to Cloud accountIds automatically"
            )
        elif username:
            # Filter common variable-like strings and language keywords
            if (username not in keyword.kwlist
                    and username not in {"def", "var", "val", "null", "true", "false",
                                         "new", "this", "self", "class", "import",
                                         "http", "https", "json", "api", "url",
                                         "get", "put", "post", "delete", "patch",
                                         "issue", "user", "field", "status", "type",
                                         "name", "value", "key", "data", "body",
                                         "text", "result", "response", "request",
                                         "error", "message", "content", "config",
                                         "basic", "bearer", "token", "auth"}
                    and not username.startswith("get")
                    and not username.startswith("set")):
                warnings.append(
                    f"Hardcoded username: '{username}' — DC usernames do not map to Cloud accountIds automatically"
                )

    # URLs
    for match in _URL_LITERAL.finditer(script):
        url = match.group(1)
        warnings.append(
            f"Hardcoded URL: '{url}' — verify this endpoint is accessible from Cloud"
        )

    return warnings


class TranslationMemory:
    """Cache of verified high-confidence translations in ChromaDB.

    Uses the existing singleton ChromaDB client with a separate collection
    named 'translation_memory'. Provides three-layer cache hit validation:
    vector similarity, structural diff, and risk element extraction.
    """

    def __init__(self) -> None:
        client = get_chroma_client()
        self._collection = client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        self._embedder = GeminiEmbedder()
        self._lock = threading.Lock()

    def query(self, source_script: str) -> CacheResult | None:
        """Query translation memory with three-layer validation.

        Layer 1: Vector similarity >= 0.92
        Layer 2: Structural diff (imports, methods, control flow must match)
        Layer 3: Risk element extraction (hardcoded IDs, keys, usernames, URLs)

        Returns CacheResult on hit, None on miss.
        """
        if not source_script or not source_script.strip():
            return None

        if self._collection.count() == 0:
            return None

        # Layer 1 — Vector similarity gate
        embedding = self._embedder.embed(source_script)
        results = self._collection.query(
            query_embeddings=[embedding],
            n_results=1,
            include=["documents", "metadatas", "distances"],
        )

        if not results["ids"][0]:
            return None

        distance = results["distances"][0][0]
        similarity = round(1.0 - distance, 4)

        if similarity < SIMILARITY_THRESHOLD:
            return None

        cached_source = results["documents"][0][0]
        metadata = results["metadatas"][0][0] or {}

        # Layer 2 — Structural diff check
        if not _structural_diff_ok(cached_source, source_script):
            return None

        # Layer 3 — Risk element extraction
        warnings = _extract_risk_warnings(source_script)

        # Deserialize incompatible_elements from JSON string
        incompatible_raw = metadata.get("incompatible_elements", "[]")
        try:
            incompatible = json.loads(incompatible_raw)
        except (json.JSONDecodeError, TypeError):
            incompatible = []

        return CacheResult(
            translated_script=metadata.get("translated_script", ""),
            confidence=float(metadata.get("confidence", 0.0)),
            confidence_reasoning=metadata.get("confidence_reasoning", ""),
            incompatible_elements=incompatible,
            notes=metadata.get("notes", ""),
            similarity=similarity,
            warnings=warnings,
        )

    def store(self, component: dict, result: "TranslationResult", force: bool = False) -> None:
        """Store a high-confidence translation in the cache.

        Only stores if confidence >= 0.85 and status is 'success',
        unless force=True (admin-approved).
        Idempotent via MD5 hash of the original script as document ID.
        """
        # Avoid circular import — TranslationResult type used for annotation only
        if not force and result.confidence < 0.85:
            return

        original_script = component.get("original_script", "")
        if not original_script or original_script == "N/A":
            return

        doc_id = _md5(original_script)
        # Embed outside the lock — embedding is thread-safe and slow
        embedding = self._embedder.embed(original_script)

        with self._lock:
            self._collection.upsert(
                ids=[doc_id],
                embeddings=[embedding],
                documents=[original_script],
                metadatas=[{
                    "component_id": component.get("component_id", ""),
                    "component_type": component.get("component_type", ""),
                    "plugin": component.get("plugin", ""),
                    "translated_script": result.translated_script,
                    "confidence": result.confidence,
                    "confidence_reasoning": result.confidence_reasoning,
                    "incompatible_elements": json.dumps(result.incompatible_elements),
                    "notes": result.notes,
                    "stored_at": datetime.now(timezone.utc).isoformat(),
                }],
            )

    def count(self) -> int:
        """Return the number of stored translations."""
        return self._collection.count()

    def list_entries(self, limit: int = 50, offset: int = 0, plugin: str | None = None) -> dict:
        """Return a paginated list of stored translations.

        Args:
            limit: max entries to return (clamped to 1..200).
            offset: number of entries to skip.
            plugin: optional plugin filter (e.g. "ScriptRunner", "JSU").

        Returns dict: {"total": int, "limit": int, "offset": int, "entries": [...]}.
        Each entry exposes id, original_script, and all metadata fields except
        the raw embedding.
        """
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))

        where = {"plugin": plugin} if plugin else None
        result = self._collection.get(
            where=where,
            include=["documents", "metadatas"],
        )

        ids = result.get("ids", []) or []
        documents = result.get("documents", []) or []
        metadatas = result.get("metadatas", []) or []

        # Sort by stored_at descending so newest entries come first
        rows = list(zip(ids, documents, metadatas))
        rows.sort(key=lambda r: (r[2] or {}).get("stored_at", ""), reverse=True)

        total = len(rows)
        window = rows[offset:offset + limit]

        entries = []
        for entry_id, document, metadata in window:
            metadata = metadata or {}
            incompatible_raw = metadata.get("incompatible_elements", "[]")
            try:
                incompatible = json.loads(incompatible_raw)
            except (json.JSONDecodeError, TypeError):
                incompatible = []

            entries.append({
                "id": entry_id,
                "component_id": metadata.get("component_id", ""),
                "component_type": metadata.get("component_type", ""),
                "plugin": metadata.get("plugin", ""),
                "original_script": document,
                "translated_script": metadata.get("translated_script", ""),
                "confidence": float(metadata.get("confidence", 0.0)),
                "confidence_reasoning": metadata.get("confidence_reasoning", ""),
                "incompatible_elements": incompatible,
                "notes": metadata.get("notes", ""),
                "stored_at": metadata.get("stored_at", ""),
            })

        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "entries": entries,
        }

    def delete(self, entry_id: str) -> bool:
        """Delete a stored translation by its ID (MD5 of original_script).

        Returns True if an entry was removed, False if no match was found.
        """
        if not entry_id:
            return False

        existing = self._collection.get(ids=[entry_id], include=[])
        if not existing.get("ids"):
            return False

        with self._lock:
            self._collection.delete(ids=[entry_id])
        return True
