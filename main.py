"""Deployment entry point for hosts that import ``main:app`` from the project root."""

from backend.app.main import app

__all__ = ["app"]
