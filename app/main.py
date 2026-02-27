from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes_auth import router as auth_router
from .routes_users import router as users_router
from .routes_kanban import router as kanban_router

app = FastAPI(title="Sky Command Base Backend", version="0.1.0")

app.add_middleware(
  CORSMiddleware,
  allow_origins=[
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:5176",
    "http://localhost:5176",
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
