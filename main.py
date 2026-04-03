from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import migration

app = FastAPI(
    title="Jira Migration Backend",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten this in production
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(migration.router, prefix="/api/v1")

@app.get("/")
def health_check():
    return {"status": "ok"}