"""FastAPI application entrypoint for ExpenseFlow."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI

from app.db import init_db
from app.routes import router

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Initialize the database schema on startup."""
    init_db()
    yield


app = FastAPI(title="ExpenseFlow", lifespan=lifespan)
app.include_router(router)
