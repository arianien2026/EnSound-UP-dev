from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from urllib.parse import urlsplit
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.orders import router as orders_router
from app.api.checkout import router as checkout_router
from app.api.ecpay_return import router as ecpay_return_router
from app.api.access import router as access_router
from app.database import get_engine
from app.dev_frontend import dev_frontend_url
from app.dev_e2e import development_e2e_gate


from app.api.dev_gmail_smoke import router as gmail_smoke_router

app = FastAPI()
app.middleware("http")(development_e2e_gate)
frontend_url = dev_frontend_url()
if frontend_url:
    app.add_middleware(CORSMiddleware, allow_origins=[
        f"{urlsplit(frontend_url).scheme}://{urlsplit(frontend_url).netloc}"
    ], allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type", "X-Device-Id"],
        allow_credentials=False)
app.include_router(orders_router)
app.include_router(checkout_router)
app.include_router(ecpay_return_router)
app.include_router(access_router)
app.include_router(gmail_smoke_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "ensound-up-payment"}


@app.get("/health/db")
def database_health() -> dict[str, str]:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1")).scalar_one()
    except (SQLAlchemyError, RuntimeError, ValueError):
        raise HTTPException(status_code=503, detail="Database unavailable") from None
    return {"status": "ok", "database": "reachable"}
