from pathlib import Path
from decimal import Decimal
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient

import app
from ledger import Ledger
from portfolio import create_portfolio, initialize, migrate_ledger_schema
from test_ledger import feed


class LegacyApiTests(TestCase):
    def test_legacy_contract_across_schema_versions(self):
        for mode in ("legacy", "migrated", "multi"):
            with self.subTest(mode=mode), TemporaryDirectory() as directory:
                path = Path(directory) / "test.sqlite3"
                initialize(path)
                if mode != "legacy":
                    migrate_ledger_schema(path)
                if mode == "multi":
                    create_portfolio(path, "Other", "USDT", "1000", "other", migrate=False)
                    other = Ledger(path, portfolio_id=2).history()

                thin = True

                def market(endpoint):
                    book = feed(endpoint)
                    if not thin and endpoint == "markets":
                        book[0]["stockPrec"] = "8"
                    if thin and endpoint.startswith("orderbook/BTC_USDT"):
                        book["bids"] = [["99", ".06"], ["98", ".04"]]
                    return book

                with patch.object(app, "Ledger", side_effect=lambda path, **kw: Ledger(path, market, **kw)):
                    client = TestClient(app.create_app(path, start_workers=False), raise_server_exceptions=False)
                    headers = {"X-Crypto-Control": "paper-start"}
                    risk = dict(paused=True, daily_loss_percent="2", drawdown_percent="5", target_percent="2")
                    portfolio = client.get("/api/v1/portfolio")
                    self.assertEqual(portfolio.status_code, 200)
                    self.assertEqual(portfolio.json()["initial_capital_minor"], 1000000)
                    self.assertEqual(client.put("/api/v1/paper/risk", json=risk, headers=headers).json(), risk)
                    self.assertEqual(Ledger(path).history()["risk"], risk)
                    self.assertEqual(client.post("/api/v1/paper/start", headers=headers).status_code, 503)
                    risk["paused"] = False
                    self.assertEqual(client.put("/api/v1/paper/risk", json=risk, headers=headers).status_code, 200)
                    self.assertEqual(client.post("/api/v1/paper/start", headers=headers).status_code, 200)
                    preview = client.get("/api/v1/paper/sale-preview", params={"symbol": "BTC"}).json()
                    self.assertEqual(preview["net_uah"], "393.61")
                    self.assertEqual(len(preview["sales"][0]["fills"]), 2)
                    body = dict(symbol="BTC", operation_id="test-sale", fingerprint=preview["fingerprint"])
                    result = client.post("/api/v1/paper/sell", headers=headers, json=body).json()
                    self.assertEqual(result["net_uah"], preview["net_uah"])
                    self.assertEqual(result["sales"][0]["net_uah"], preview["sales"][0]["net_uah"])
                    self.assertEqual(client.post("/api/v1/paper/sell", headers=headers, json=body).json(), result)
                    self.assertEqual(client.get("/api/v1/paper").status_code, 200)
                    self.assertEqual(client.get("/api/v1/paper/history").json()["revision"], 1)
                    self.assertEqual(client.get("/api/v1/paper/observations").json(), {"observations": []})
                    self.assertEqual(client.post("/api/v1/paper/sell", headers=headers, json={}).status_code, 422)
                    thin = False
                    full = client.get("/api/v1/paper/sale-preview", params={"symbol": "all"}).json()
                    remaining = full["sales"][0]["remaining_quantity"]
                    self.assertIsInstance(remaining, str)
                    self.assertEqual(Decimal(remaining), 0)
                    body.update(symbol="all", operation_id="close", fingerprint=full["fingerprint"])
                    self.assertEqual(client.post("/api/v1/paper/sell", headers=headers, json=body).json()["net_uah"],
                                     full["net_uah"])
                    self.assertEqual(Ledger(path).history()["holdings"], [])
                if mode == "multi":
                    self.assertEqual(Ledger(path, portfolio_id=2).history(), other)
