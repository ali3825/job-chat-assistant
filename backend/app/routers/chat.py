from __future__ import annotations

from fastapi import APIRouter, Depends, File, Request, UploadFile
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import ClientId, get_orchestrator
from ..errors import AppError
from ..schemas import ChatRequest, ChatResponse, ProfileUpdate
from ..services.orchestrator import Orchestrator
from ..services.resume_parser import ResumeError, extract_text, parse_resume_text, validate_upload

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=ChatResponse, summary="Send a chat message")
def chat(body: ChatRequest, client_id: ClientId, db: Session = Depends(get_db),
         orch: Orchestrator = Depends(get_orchestrator)):
    return orch.handle_message(db, client_id, body.message.strip())


@router.post("/resume/parse", response_model=ChatResponse, summary="Upload and parse a PDF/DOCX resume")
def parse_resume(request: Request, client_id: ClientId, file: UploadFile = File(...), orch: Orchestrator = Depends(get_orchestrator)):
    s = request.app.state.settings
    max_bytes = s.max_upload_mb * 1024 * 1024
    # Read at most max+1 bytes so an oversized upload can't exhaust memory. Kept in memory only.
    data = file.file.read(max_bytes + 1)
    try:
        ext = validate_upload(file.filename or "", data, max_bytes)
        text, warnings = extract_text(data, ext, s.max_pdf_pages)
        profile = parse_resume_text(text)
    except ResumeError as e:
        raise AppError(e.status, e.code, e.message, e.recovery)
    finally:
        del data
    return orch.after_resume_parsed(client_id, profile, warnings)


@router.patch("/profile", response_model=ChatResponse, summary="Correct fields of the parsed profile")
def patch_profile(body: ProfileUpdate, client_id: ClientId, orch: Orchestrator = Depends(get_orchestrator)):
    return orch.update_profile(client_id, body)


@router.get("/profile", response_model=ChatResponse, summary="Current session profile")
def get_profile(client_id: ClientId, orch: Orchestrator = Depends(get_orchestrator)):
    sess = orch.sessions.get(client_id)
    if sess.profile is None:
        raise AppError(404, "no_profile", "No resume has been uploaded in this session.", "Upload your resume first.")
    return ChatResponse(reply="Current profile", profile=sess.profile)
