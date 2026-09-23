import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))
from fastapi.middleware.cors import CORSMiddleware

from .routes_auth import router as auth_router
from .routes_users import router as users_router
from .routes_kanban import router as kanban_router
from .routes_monitor import router as monitor_router
from .routes_research import (
    legacy_routers as research_legacy_routers,
    modules_router as research_modules_router,
    router as research_router,
)
from .routes_files import router as files_router
from .routes_tools import router as tools_router
from . import scheduler as sched


@asynccontextmanager
async def lifespan(app: FastAPI):
    sched.start()
    yield
    sched.stop()


app = FastAPI(title="Sky Command Base Backend", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5174",
        "http://127.0.0.1:5176",
        "http://localhost:5176",
        "http://127.0.0.1:8080",
        "http://localhost:8080",
        "http://100.92.170.72:8050",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    return {"status": "ok"}


app.include_router(auth_router)
app.include_router(users_router)
app.include_router(kanban_router)
app.include_router(monitor_router)
app.include_router(research_modules_router)
app.include_router(research_router)
for _r in research_legacy_routers:
    app.include_router(_r)
app.include_router(files_router)
app.include_router(tools_router)
