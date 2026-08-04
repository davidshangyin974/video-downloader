"""ASGI entry point.

Keep this module deliberately small so Uvicorn, desktop packaging, and tests use
one stable import while API routes and download services evolve independently.
"""

from .server import app

__all__ = ["app"]
