from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from app.models import AnalysisReport
from app.services.router import route_components, route_components_stream
from app.services.validator import validate_results
from app.services.packager import package_results
import json
import os

router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/migrate")
def migrate(report: AnalysisReport):
    try:
        results = route_components(report)
        valid, flagged = validate_results(results)
        zip_path = package_results(valid, flagged, report.analysis_id)
        return FileResponse(
            path=zip_path,
            media_type="application/zip",
            filename=f"migration_{report.analysis_id}.zip"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/migrate/stream")
def migrate_stream(report: AnalysisReport):
    def event_generator():
        results = []

        for event in route_components_stream(report):
            yield f"data: {json.dumps(event)}\n\n"

            if event.get("type") == "agent_done" and event.get("result") is not None:
                results.append(event["result"])

        valid, flagged = validate_results(results)
        yield f"data: {json.dumps({'type': 'validation', 'valid': len(valid), 'flagged': len(flagged)})}\n\n"

        zip_path = package_results(valid, flagged, report.analysis_id)
        yield f"data: {json.dumps({'type': 'done', 'zip_path': zip_path, 'analysis_id': report.analysis_id})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/download")
def download_zip(path: str):
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        path=path,
        media_type="application/zip",
        filename=os.path.basename(path)
    )