from fastapi import FastAPI

from backend.app.api.routes import router

app = FastAPI(
    title="Service Intelligence API",
    version="0.14.0",
    description="Auditable work-order extraction and validation.",
)
app.include_router(router)
