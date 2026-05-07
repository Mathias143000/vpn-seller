from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()

GUIDE_PDF = Path(__file__).resolve().parents[2] / "assets" / "МОЙ-ПУТЕВОДИТЕЛЬ.pdf"


@router.get("/files/setup-guide")
async def setup_guide_file() -> FileResponse:
    if not GUIDE_PDF.exists():
        raise HTTPException(status_code=404, detail="Guide PDF is missing")
    return FileResponse(GUIDE_PDF, media_type="application/pdf", filename=GUIDE_PDF.name)
