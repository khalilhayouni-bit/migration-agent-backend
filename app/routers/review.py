from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import List, Optional

from app.auth.security import get_current_user, require_admin
from app.models import TranslationResult

router = APIRouter(tags=["review"])


def _get_memory():
    """Return the TranslationMemory singleton or 503."""
    try:
        from rag import get_translation_memory
        memory = get_translation_memory()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Translation memory unavailable: {e}")
    if memory is None:
        raise HTTPException(status_code=503, detail="Translation memory unavailable")
    return memory


class ReviewItem(BaseModel):
    component_id: str
    component_type: str
    plugin: str
    original_script: str
    translated_script: str
    confidence: float
    confidence_reasoning: str = ""
    incompatible_elements: List[str] = []
    notes: str = ""


class ReviewRequest(BaseModel):
    approved: List[ReviewItem]


class ReviewResponse(BaseModel):
    stored: int
    message: str


@router.post("/review", response_model=ReviewResponse)
def submit_review(body: ReviewRequest, _admin: dict = Depends(require_admin)):
    """Store admin-approved translations in ChromaDB translation memory."""
    memory = _get_memory()

    stored = 0
    for item in body.approved:
        if not item.original_script or not item.translated_script:
            continue

        # Build component dict and TranslationResult for the memory store
        component = {
            "component_id": item.component_id,
            "component_type": item.component_type,
            "plugin": item.plugin,
            "original_script": item.original_script,
        }

        result = TranslationResult(
            translated_script=item.translated_script,
            confidence=item.confidence,
            confidence_reasoning=item.confidence_reasoning,
            incompatible_elements=item.incompatible_elements,
            notes=item.notes,
        )

        # Admin approval overrides the 0.85 confidence gate
        memory.store(component, result, force=True)
        stored += 1

    return ReviewResponse(stored=stored, message=f"{stored} translation(s) saved to memory.")


class MemoryEntry(BaseModel):
    id: str
    component_id: str
    component_type: str
    plugin: str
    original_script: str
    translated_script: str
    confidence: float
    confidence_reasoning: str
    incompatible_elements: List[str]
    notes: str
    stored_at: str


class MemoryListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    entries: List[MemoryEntry]


@router.get("/memory", response_model=MemoryListResponse)
def list_memory(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    plugin: Optional[str] = Query(None, description="Filter by plugin (ScriptRunner, JSU, Webhook, native, MISC)"),
    _admin: dict = Depends(require_admin),
):
    """List entries stored in the translation memory (paginated, newest first)."""
    memory = _get_memory()
    try:
        return memory.list_entries(limit=limit, offset=offset, plugin=plugin)
    except Exception as e:
        print(f"[Memory] List failed: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail="Failed to list translation memory.")


@router.delete("/memory/{entry_id}")
def delete_memory(entry_id: str, _admin: dict = Depends(require_admin)):
    """Delete a single translation memory entry by its ID (MD5 of original_script).

    Idempotent: returns 200 with `existed=false` if the entry was already
    gone, so client-side retries on transient network errors don't surface
    as 404s after the first call succeeded.
    """
    memory = _get_memory()
    try:
        removed = memory.delete(entry_id)
    except Exception as e:
        print(f"[Memory] Delete failed: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete translation memory entry.")

    return {"deleted": entry_id, "existed": removed}
