import asyncio
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from decimal import Decimal
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from portfolio import initialize, read_portfolio
from ledger import Ledger, MarketUnavailable, OperationConflict
from quotes import Quotes

ROOT = Path(__file__).resolve().parent
SaleSymbol = Literal["all", "BTC", "ETH", "SOL", "XRP", "BNB", "LINK", "LTC", "ADA", "TRX"]


class ConfirmSale(BaseModel):
    symbol: SaleSymbol
    operation_id: str = Field(min_length=1, max_length=100)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class RiskSettings(BaseModel):
    paused: bool
    daily_loss_percent: Decimal = Field(gt=0, le=100)
    drawdown_percent: Decimal = Field(gt=0, le=100)
    target_percent: Decimal = Field(gt=0, le=100)


async def history_loop(ledger, interval=300, sleep=asyncio.sleep, record=None):
    """Record persisted observations until the FastAPI lifespan cancels us."""
    while True:
        if record is None:
            await asyncio.to_thread(ledger.record_valuation)
        else:
            await record()
        await sleep(interval)


def require_paper_header(value):
    if value != "paper-start":
        raise HTTPException(400, "Expected X-Crypto-Control: paper-start")


def create_app(db_path=ROOT / "data" / "crypto_control.sqlite3"):
    quotes = Quotes()
    ledger = Ledger(db_path, fee_override=os.getenv("CRYPTO_CONTROL_TAKER_FEE_PERCENT"))

    async def poll_quotes():
        while True:
            delay = await asyncio.to_thread(quotes.refresh)
            await asyncio.sleep(delay)

    @asynccontextmanager
    async def lifespan(app):
        initialize(db_path)
        ledger.initialize()
        quote_worker = asyncio.create_task(poll_quotes())
        history_worker = asyncio.create_task(history_loop(ledger))
        try:
            yield
        finally:
            quote_worker.cancel()
            history_worker.cancel()
            with suppress(asyncio.CancelledError):
                await quote_worker
            with suppress(asyncio.CancelledError):
                await history_worker

    app = FastAPI(title="Crypto Control", version="1.0.0", lifespan=lifespan)

    @app.get("/", include_in_schema=False)
    def home():
        return FileResponse(ROOT / "index.html", media_type="text/html")

    @app.get("/api/v1/status")
    def status() -> dict[str, str]:
        return {"status": "ready", "mode": "demo", "quotes": quotes.snapshot()["connection"]}

    @app.get("/api/v1/quotes")
    def current_quotes() -> dict:
        """WhiteBIT spot prices as decimal strings and their UTC receipt times."""
        return quotes.snapshot()

    @app.get("/api/v1/portfolio")
    def portfolio() -> dict:
        return read_portfolio(db_path)

    @app.get("/api/v1/paper")
    def paper(response: Response) -> dict:
        response.headers["Cache-Control"] = "no-store"
        return ledger.state()

    @app.get("/api/v1/paper/history")
    def paper_history() -> dict:
        return ledger.history()

    @app.get("/api/v1/paper/observations")
    def paper_observations() -> dict:
        return {"observations": ledger.observation_history()}

    @app.post("/api/v1/paper/start")
    def start_paper(x_crypto_control: str = Header()) -> dict:
        require_paper_header(x_crypto_control)
        try:
            return ledger.start()
        except MarketUnavailable as error:
            raise HTTPException(503, str(error)) from error

    @app.get("/api/v1/paper/sale-preview")
    def sale_preview(symbol: SaleSymbol, response: Response) -> dict:
        response.headers["Cache-Control"] = "no-store"
        try:
            return ledger.preview(symbol)
        except MarketUnavailable as error:
            raise HTTPException(503, str(error)) from error

    @app.post("/api/v1/paper/sell")
    def sell(body: ConfirmSale, x_crypto_control: str = Header()) -> dict:
        require_paper_header(x_crypto_control)
        try:
            return ledger.sell(body.symbol, body.operation_id, body.fingerprint)
        except OperationConflict as error:
            raise HTTPException(409, str(error)) from error
        except MarketUnavailable as error:
            raise HTTPException(503, str(error)) from error

    @app.put("/api/v1/paper/risk")
    def risk(body: RiskSettings, x_crypto_control: str = Header()) -> dict:
        require_paper_header(x_crypto_control)
        return ledger.configure(**body.model_dump(mode="json"))

    return app


app = create_app()
