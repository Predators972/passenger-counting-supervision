"""
Main entry point for the supervision tool backend.

Run locally with:
    uvicorn app.main:app --reload

The API is served under /api, and the front-end static files are served
directly from this same app so you only need to run one process locally.
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.routes import vehicles

app = FastAPI(title="Outil de supervision - comptage voyageurs")

app.include_router(vehicles.router)

# Serve the front-end (plain HTML/CSS/JS, no build step required)
FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
