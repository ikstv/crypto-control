import asyncio
import logging
import sqlite3
from shadow import Shadow, SOURCE
from algorithm import Algorithm, Blocked
import os
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from decimal import Decimal
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from portfolio import (initialize, read_portfolio, ledger_schema_migrated,
                       existing_portfolio_ids, list_existing_portfolios,
                       create_portfolio, read_portfolio_for)
from ledger import Ledger, MarketUnavailable, OperationConflict, LedgerSchemaMismatch
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


class CreatePortfolio(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    currency: Literal["UAH", "USDT"]
    initial_capital: Decimal
    idempotency_key: str = Field(min_length=1, max_length=200)


class PortfolioSale(BaseModel):
    symbol: SaleSymbol
    operation_id: str = Field(min_length=1, max_length=100)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    quantity: Decimal | None = None


async def history_loop(ledger, interval=300, sleep=asyncio.sleep, record=None):
    """Record persisted observations until the FastAPI lifespan cancels us."""
    while True:
        if record is None:
            await asyncio.to_thread(ledger.record_valuation)
        else:
            await record()
        await sleep(interval)


async def all_portfolio_history_loop(db_path, fetch, interval=300, sleep=asyncio.sleep):
    while True:
        slot = datetime.now(timezone.utc).replace(second=0, microsecond=0).isoformat()
        for portfolio_id in existing_portfolio_ids(db_path):
            await asyncio.to_thread(Ledger(db_path, fetch, portfolio_id=portfolio_id).record_valuation, slot)
        await sleep(interval)


def require_paper_header(value):
    if value != "paper-start":
        raise HTTPException(400, "Expected X-Crypto-Control: paper-start")


def create_app(db_path=ROOT / "data" / "crypto_control.sqlite3", start_workers=True):
    quotes = Quotes()
    ledger = Ledger(db_path, fee_override=os.getenv("CRYPTO_CONTROL_TAKER_FEE_PERCENT"))
    algorithm = Algorithm(db_path)
    shadow = Shadow(algorithm)

    async def poll_shadow():
        while True:
            work = asyncio.create_task(asyncio.to_thread(shadow.poll))
            try:
                await asyncio.shield(work)
            except asyncio.CancelledError:
                await work
                raise
            except sqlite3.Error:
                logging.exception("Shadow signal storage unavailable")
            await asyncio.sleep(300)

    async def poll_quotes():
        while True:
            delay = await asyncio.to_thread(quotes.refresh)
            await asyncio.sleep(delay)

    @asynccontextmanager
    async def lifespan(app):
        migrated = ledger_schema_migrated(db_path)
        if not migrated:
            initialize(db_path)
            ledger.initialize()
        quote_worker = asyncio.create_task(poll_quotes()) if start_workers else None
        history_worker = asyncio.create_task(
            all_portfolio_history_loop(db_path, quotes.fetch) if migrated
            else history_loop(ledger)
        ) if start_workers else None
        shadow_worker = asyncio.create_task(poll_shadow()) if start_workers else None
        try:
            yield
        finally:
            for worker in (quote_worker, history_worker, shadow_worker):
                if worker:
                    worker.cancel()
            with suppress(asyncio.CancelledError):
                if quote_worker:
                    await quote_worker
            with suppress(asyncio.CancelledError):
                if history_worker:
                    await history_worker
            with suppress(asyncio.CancelledError):
                if shadow_worker:
                    await shadow_worker

    app = FastAPI(title="Crypto Control", version="1.0.0", lifespan=lifespan)

    @app.get("/api/v1/algorithm")
    def algorithm_status():
        return {"mode": algorithm.status(), "strategy_available": False}

    @app.get("/api/v1/algorithm/history")
    def algorithm_history():
        return {"changes": algorithm.history()}

    @app.get("/api/v1/shadow/signals")
    def shadow_signals():
        return {"mode": algorithm.status(), "source": SOURCE, "records": shadow.history()}

    @app.post("/api/v1/algorithm/{action}")
    def algorithm_change(action: Literal["activate", "stop", "emergency", "reset"],
                         x_crypto_control: str = Header(), confirmed: bool = False):
        require_paper_header(x_crypto_control)
        try:
            return {"mode": algorithm.change(action, confirmed)}
        except Blocked as error:
            raise HTTPException(409, str(error)) from error

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

    def require_migrated():
        if not ledger_schema_migrated(db_path):
            raise HTTPException(409, "Multi-portfolio schema migration is required")

    def portfolio_ledger(portfolio_id: int):
        require_migrated()
        try:
            read_portfolio_for(db_path, portfolio_id)
        except ValueError as error:
            raise HTTPException(404, str(error)) from error
        return Ledger(db_path, portfolio_id=portfolio_id)

    @app.get("/api/v1/portfolios")
    def portfolios() -> dict:
        require_migrated()
        return {"portfolios": list_existing_portfolios(db_path)}

    @app.post("/api/v1/portfolios")
    def create_new_portfolio(body: CreatePortfolio, x_crypto_control: str = Header()) -> dict:
        require_paper_header(x_crypto_control)
        require_migrated()
        try:
            return create_portfolio(db_path, body.name, body.currency, body.initial_capital,
                                    body.idempotency_key, migrate=False)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.get("/api/v1/portfolios/{portfolio_id}")
    def portfolio_detail(portfolio_id: int) -> dict:
        ledger = portfolio_ledger(portfolio_id)
        return {**read_portfolio_for(db_path, portfolio_id), "history": ledger.history(),
                "observations": ledger.observation_history()}

    @app.post("/api/v1/portfolios/{portfolio_id}/paper/start")
    def portfolio_start(portfolio_id: int, x_crypto_control: str = Header()) -> dict:
        require_paper_header(x_crypto_control)
        try:
            return portfolio_ledger(portfolio_id).start()
        except LedgerSchemaMismatch as error:
            raise HTTPException(409, str(error)) from error
        except MarketUnavailable as error:
            raise HTTPException(503, str(error)) from error

    @app.get("/api/v1/portfolios/{portfolio_id}/paper/history")
    def portfolio_history(portfolio_id: int) -> dict:
        return portfolio_ledger(portfolio_id).history()

    @app.get("/api/v1/portfolios/{portfolio_id}/paper/observations")
    def portfolio_observations(portfolio_id: int) -> dict:
        return {"observations": portfolio_ledger(portfolio_id).observation_history()}

    @app.get("/api/v1/portfolios/{portfolio_id}/paper/sale-preview")
    def portfolio_sale_preview(portfolio_id: int, symbol: SaleSymbol) -> dict:
        try:
            return portfolio_ledger(portfolio_id).preview(symbol)
        except MarketUnavailable as error:
            raise HTTPException(503, str(error)) from error

    @app.post("/api/v1/portfolios/{portfolio_id}/paper/sell")
    def portfolio_sell(portfolio_id: int, body: PortfolioSale, x_crypto_control: str = Header()) -> dict:
        require_paper_header(x_crypto_control)
        try:
            return portfolio_ledger(portfolio_id).sell(body.symbol, body.operation_id,
                                                       body.fingerprint, body.quantity)
        except OperationConflict as error:
            raise HTTPException(409, str(error)) from error
        except MarketUnavailable as error:
            raise HTTPException(503, str(error)) from error

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
