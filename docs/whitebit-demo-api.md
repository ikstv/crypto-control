# WhiteBIT Demo API boundary

`whitebit_demo_api.py` implements a read-only private WhiteBIT API client for
DBTC/DUSDT account status. It signs requests with HMAC-SHA512, synchronizes a
millisecond nonce against WhiteBIT server time, and calls
`whitebit_demo_policy.py` immediately before dispatching every private request.
The client exposes no methods for creating or cancelling orders. Its allowlist
is defense in depth; it does not make a WhiteBIT API key demo-only.

Run `python whitebit_demo_api.py demo-status` after configuring a read-only
key. It reads DBTC and DUSDT balances, order history, and executed deals.
Results include account-wide DBTC/DUSDT activity from the same WhiteBIT
account and cannot be attributed to Crypto Control. PnL is marked unavailable:
these endpoints do not provide a trustworthy account PnL or full cost basis,
so the client does not infer one. Permission or connectivity failures return
a clear error or a `partial` result.

Credentials are read from `WHITEBIT_DEMO_API_KEY` and
`WHITEBIT_DEMO_API_SECRET`, or from systemd credential files named
`whitebit_demo_api_key` and `whitebit_demo_api_secret` under
`CREDENTIALS_DIRECTORY`. Do not put credentials in SQLite, source control,
command-line arguments, or logs. No API key has been created or supplied, so
the client has not been connected to a real account.

## Private API operations

| Purpose | Endpoint | Client behavior |
| --- | --- | --- |
| Read trade balance | `/api/v4/trade-account/balance` | Only ticker `DBTC` or `DUSDT` |
| Read order history | `/api/v4/trade-account/order/history` | Fixed `market=DBTC_DUSDT` |
| Read executed deals | `/api/v4/trade-account/executed-history` | Fixed `market=DBTC_DUSDT` |

Every private call uses POST with `request`, increasing millisecond `nonce`,
and endpoint-specific fields; compact JSON is Base64 encoded and signed with
HMAC-SHA512. The client gets server time from the public API before its first
private call. Redirects are disabled, response bodies and credentials are not
included in errors, and each request is revalidated immediately before
transport dispatch.

The policy module also contains validators for order creation and cancellation
because of earlier work, but the read-only client explicitly rejects every
route except the three reads above. Open-order queries are also rejected. An
unknown route, another pair, or any transfer, withdrawal, margin, or futures
request is blocked before network dispatch.

## API key permissions and account isolation

For this read-only integration, enable only these individual API endpoints:
`/api/v4/trade-account/balance`, `/api/v4/trade-account/order/history`, and
`/api/v4/trade-account/executed-history`. Leave **Order Management**, Deposit,
Withdrawal, Transfer between balances, Wallet balance/history, and Codes
disabled. The client does not query open orders, so it does not need any
Order Management scope.

Enable API IP restriction and allow only the server's confirmed fixed public
egress IP. Confirm that IP out-of-band before creating a key. WhiteBIT says
requests from an unlisted address can deactivate the key. Store credentials
only in the protected service credential store; never in Git, SQLite, command
arguments, or logs.

WhiteBIT documents personal sub-accounts (up to 10 for ordinary users) with
Spot balance access, API keys, and the ability to transfer funds between the
main account and sub-accounts. Its documentation does not confirm that Demo
Tokens can be issued to a sub-account or that an API key can be restricted to
demo assets. A sub-account may offer organizational separation, but it is not
a guaranteed demo-only boundary. Do not create one for this purpose until
WhiteBIT support confirms Demo Token availability and the applicable rules.

No key was created, no real account queried, no server or service changed, and
the running WhiteBIT Demo Grid was not accessed or changed. Client-side
allowlisting cannot protect against code that bypasses the client or a stolen
key with broader permissions.

## Sources

- [WhiteBIT API platform and permissions](https://help.whitebit.com/hc/en-gb/articles/9787172186397-What-is-WhiteBIT-API)
- [Private API authentication](https://docs.whitebit.com/api-reference/authentication)
- [First API call and server-time endpoint](https://docs.whitebit.com/guides/first-api-call)
- [WhiteBIT sub-accounts](https://help.whitebit.com/hc/en-gb/articles/15908732354333-Sub-Accounts-on-WhiteBIT)
- [Demo Token guide](https://help.whitebit.com/hc/en-gb/articles/15721321138461-What-is-Demo-Token-and-how-to-get-it)
- [Trade balance](https://docs.whitebit.com/api-reference/trading/trading-balance)
- [Open orders](https://docs.whitebit.com/api-reference/trading/query-unexecuted-orders)
- [Order history](https://docs.whitebit.com/api-reference/trading/query-executed-orders)
- [Executed deals](https://docs.whitebit.com/api-reference/trading/query-executed-order-history)
