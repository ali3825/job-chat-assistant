import logging
import time

from fastapi import FastAPI, Request


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def install_request_logging(app: FastAPI) -> None:
    log = logging.getLogger("app.request")

    @app.middleware("http")
    async def _log(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        # Path + status + duration only: never bodies, headers, or client ids.
        log.info("%s %s -> %s (%.0f ms)", request.method, request.url.path, response.status_code,
                 (time.perf_counter() - start) * 1000)
        return response
