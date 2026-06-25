from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum
from sqlalchemy import text

from .config import get_settings
from .db import engine
from .routers import entity, extract, ingest, link, query, search, subgraph, timeline, update

load_dotenv()
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))
    yield
    await engine.dispose()


app = FastAPI(
    title="Central KG API",
    version="0.1.0",
    description="Real-time agent context API backed by a Postgres+pgvector knowledge graph.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "model": get_settings().anthropic_model}


app.include_router(ingest.router, tags=["ingest"])
app.include_router(extract.router, tags=["extract"])
app.include_router(link.router, tags=["graph"])
app.include_router(query.router, tags=["query"])
app.include_router(search.router, tags=["query"])
app.include_router(entity.router, tags=["query"])
app.include_router(subgraph.router, tags=["query"])
app.include_router(update.router, tags=["events"])
app.include_router(timeline.router, tags=["events"])

# AWS Lambda entrypoint
handler = Mangum(app, lifespan="off")
