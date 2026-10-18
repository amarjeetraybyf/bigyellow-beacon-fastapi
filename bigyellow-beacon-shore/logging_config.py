# -*- coding: utf-8 -*-
"""
Centralized Application Logging & Request Tracking for BigYellow Beacon Shore Server.
Supports DEBUG, INFO, WARNING, ERROR levels.
Logs to both Console (stdout) and Rotating Log File (logs/shore_app.log).
"""

import os
import sys
import time
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from config import LOG_LEVEL, LOG_FILE_PATH, DEBUG

# Ensure logs directory exists
log_file = Path(LOG_FILE_PATH)
log_file.parent.mkdir(parents=True, exist_ok=True)

# Logger name
LOGGER_NAME = "shore"
logger = logging.getLogger(LOGGER_NAME)


def setup_logging():
    """Initializes console and rotating file logging handlers."""
    level = getattr(logging, LOG_LEVEL, logging.DEBUG if DEBUG else logging.INFO)
    logger.setLevel(level)

    # Avoid duplicate handlers if reloaded
    if not logger.handlers:
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # 1. Console Stream Handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        # 2. Rotating File Handler (10 MB per file, max 5 backup files)
        file_handler = RotatingFileHandler(
            filename=str(log_file),
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


# Initialize immediately upon module import
setup_logging()


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware logging HTTP method, path, response status, duration in ms,
    and client IP for every incoming API call, plus warnings on 4xx and errors on 5xx/exceptions.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        start_time = time.perf_counter()

        # Extract client IP (handle proxies & direct clients)
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()
        elif request.client and request.client.host:
            client_ip = request.client.host
        else:
            client_ip = "unknown"

        method = request.method
        path = request.url.path
        query = str(request.url.query)
        url_display = f"{path}?{query}" if query else path

        # Debug log on incoming request
        logger.debug(f"--> [{client_ip}] {method} {url_display}")

        try:
            response = await call_next(request)
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            status_code = response.status_code

            log_msg = f"[{client_ip}] {method} {url_display} -> {status_code} ({duration_ms:.2f}ms)"

            if status_code >= 500:
                logger.error(log_msg)
            elif status_code >= 400:
                logger.warning(log_msg)
            else:
                logger.info(log_msg)

            # Attach performance header
            response.headers["X-Response-Time-Ms"] = f"{duration_ms:.2f}"
            return response

        except Exception as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            logger.error(
                f"[{client_ip}] {method} {url_display} -> 500 Internal Error ({duration_ms:.2f}ms): {exc}",
                exc_info=True,
            )
            raise exc
