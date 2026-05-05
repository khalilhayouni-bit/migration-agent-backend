from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List

from app.auth.security import get_current_user
from app.models import TranslationResult

router = APIRouter(tags=["review"])


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
def submit_review(body: ReviewRequest, _user: dict = Depends(get_current_user)):
    """Store admin-approved translations in ChromaDB translation memory."""
    try:
        from rag.translation_memory import TranslationMemory
        memory = TranslationMemory()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Translation memory unavailable: {e}")

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
