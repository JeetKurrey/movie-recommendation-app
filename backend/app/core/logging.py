"""
Minimal structured-ish logging setup.

Keeps the PRD's NFR-Observability requirement (basic logging of requests,
token usage, error rates) without pulling in a heavy logging stack.
"""
import logging
import sys


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if root.handlers:
        # Already configured (e.g. reloaded by uvicorn) — avoid duplicate handlers.
        return

    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(level)

    # Quiet down noisy third-party loggers unless something goes wrong.
    logging.getLogger("httpx").setLevel(logging.WARNING)
