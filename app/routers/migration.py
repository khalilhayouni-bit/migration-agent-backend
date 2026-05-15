from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from app.config import settings
from app.models import AnalysisReport, Component
from app.services.router import route_components, route_components_stream, AGENT_MAP, _misc_fallback
from app.services.validator import validate_results
from app.services.packager import package_results
from app.auth.security import get_current_user
import json
import os
import traceback

router = APIRouter()


@router.get("/health")
def health():
    """Deep health check: verifies Gemini key is configured and reports
    ChromaDB / translation-memory availability. Returns 503 if a hard
    dependency (Gemini key) is missing."""
    checks: dict = {}

    gemini_ok = bool(settings.gemini_api_key and settings.gemini_api_key.strip())
    checks["gemini_api_key"] = "ok" if gemini_ok else "missing"

    try:
        from rag.chroma_client import get_chroma_collection
        checks["rag_documents"] = get_chroma_collection().count()
    except Exception as e:
        checks["rag_documents"] = f"unavailable: {type(e).__name__}"

    try:
        from rag import get_translation_memory
        mem = get_translation_memory()
        checks["translation_memory"] = mem.count() if mem is not None else "unavailable"
    except Exception as e:
        checks["translation_memory"] = f"unavailable: {type(e).__name__}"

    status_ok = gemini_ok
    body = {"status": "ok" if status_ok else "degraded", "checks": checks}
    return JSONResponse(status_code=200 if status_ok else 503, content=body)


@router.get("/stats")
def get_stats(_user: dict = Depends(get_current_user)):
    memory_count = 0
    rag_count = 0
    try:
        from rag import get_translation_memory
        mem = get_translation_memory()
        if mem is not None:
            memory_count = mem.count()
    except Exception:
        pass
    try:
        from rag.chroma_client import get_chroma_collection
        rag_count = get_chroma_collection().count()
    except Exception:
        pass
    return {
        "memory_translations": memory_count,
        "rag_documents": rag_count,
    }


@router.post("/migrate")
async def migrate(report: AnalysisReport, _user: dict = Depends(get_current_user)):
    try:
        results = await route_components(report)
        valid, flagged = validate_results(results)
        zip_path = package_results(valid, flagged, report.analysis_id)
        return FileResponse(
            path=zip_path,
            media_type="application/zip",
            filename=f"migration_{report.analysis_id}.zip"
        )
    except Exception as e:
        print(f"[Migrate] Error: {e}")
        raise HTTPException(status_code=500, detail="Migration failed. Check server logs for details.")


@router.post("/migrate/stream")
def migrate_stream(report: AnalysisReport, _user: dict = Depends(get_current_user)):
    def event_generator():
        results = []

        try:
            for event in route_components_stream(report):
                # Keepalive events become SSE comment lines: they keep the
                # HTTP connection from being torn down by idle timers but
                # are invisible to the EventSource consumer.
                if event.get("type") == "keepalive":
                    yield ": keepalive\n\n"
                    continue

                yield f"data: {json.dumps(event)}\n\n"

                if event.get("type") == "agent_done" and event.get("result") is not None:
                    results.append(event["result"])

            valid, flagged = validate_results(results)
            yield f"data: {json.dumps({'type': 'validation', 'valid': len(valid), 'flagged': len(flagged)})}\n\n"

            zip_path = package_results(valid, flagged, report.analysis_id)
            yield f"data: {json.dumps({'type': 'done', 'zip_path': zip_path, 'analysis_id': report.analysis_id})}\n\n"
        except Exception as e:
            print(f"[MigrateStream] Error: {type(e).__name__}: {e}")
            traceback.print_exc()
            error_event = {
                "type": "error",
                "message": "Migration stream failed. Check server logs for details.",
                "error_type": type(e).__name__,
            }
            yield f"data: {json.dumps(error_event)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/retry")
async def retry_component(component: Component, _user: dict = Depends(get_current_user)):
    """Re-run a single component through its agent.

    Useful when one component out of a larger migration failed or produced a
    low-confidence result. Returns the same result shape as a streamed
    `agent_done` event, plus a `validation_issues` list so the frontend can
    show whether the retry passed validation.
    """
    agent = AGENT_MAP.get(component.plugin, _misc_fallback)
    print(f"[Retry] Re-running component '{component.component_id}' -> {component.plugin.value}")

    try:
        result = await agent.async_translate(component)
    except Exception as e:
        print(f"[Retry] Error for '{component.component_id}': {type(e).__name__}: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Retry failed. Check server logs for details.")

    valid, flagged = validate_results([result])
    final = (valid + flagged)[0]
    return {
        "result": final,
        "passed_validation": len(valid) == 1,
        "validation_issues": final.get("validation_issues", []),
    }


ALLOWED_OUTPUT_DIR = os.path.abspath("app/outputs")


@router.get("/download")
def download_zip(path: str, _user: dict = Depends(get_current_user)):
    abs_path = os.path.abspath(path)
    if not abs_path.startswith(ALLOWED_OUTPUT_DIR):
        raise HTTPException(status_code=403, detail="Access denied")
    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        path=abs_path,
        media_type="application/zip",
        filename=os.path.basename(abs_path)
    )