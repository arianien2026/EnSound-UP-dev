from fastapi import FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.orders import router as orders_router
from app.api.checkout import router as checkout_router
from app.database import get_engine


app = FastAPI()
app.include_router(orders_router)
app.include_router(checkout_router)


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
