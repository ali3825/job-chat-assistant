from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


@router.get("/health")
def health(request: Request):
    store = request.app.state.store
    llm = request.app.state.orchestrator.llm
    stats = store.stats()
    return {
        "status": "ok",
        "dataset": stats,
        "llm": {"configured": llm is not None, "provider": llm.name if llm else None,
                "mode": "llm" if llm else "fallback"},
        "notice": ("Running in deterministic fallback mode (no LLM configured)." if not llm else None),
    }
