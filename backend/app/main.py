## @file main.py
#  @brief Application entry point: creates the FastAPI app, registers the API
#  routers and mounts the front-end static files under a single process.

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.routes import vehicles, stats

app = FastAPI(title="Outil de supervision - comptage voyageurs")

app.include_router(vehicles.router)
app.include_router(stats.router)

## Directory containing the front-end static files (HTML/CSS/JS).
FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"

app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
