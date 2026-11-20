# Crypto Control Mobile

The panel serves HTTP on Ubuntu loopback only (`127.0.0.1:18091`). Its
systemd service polls WhiteBIT about once per minute with the existing
three-endpoint read-only client. The browser receives a status, two demo
balances and counts from the first history page, never exchange credentials.
An unavailable balance is displayed as a dash. The panel does not cache data
for offline use.

The engine status is the state of `crypto-control-paper.service`. The
decision log reads the last 30 persisted BUY/SELL/HOLD decisions from
`experiment.sqlite3` in SQLite read-only mode. The mobile service has a
separate user. If Paper Trading is enabled in the future, provide a separate
read-only export or explicitly arrange read permission for the log, including
SQLite WAL access if applicable. Do not grant access to the entire trading
state directory just to make the log visible. Inaccessible data is reported
as unavailable.

## Credentials and startup

Install the dashboard files under `/opt/crypto-control` and the unit from
`deploy/crypto-control-mobile.service` as an independent systemd service.
From an authorized SSH terminal, the owner runs this block and types each
value directly at its hidden prompt. Use a unique random ASCII password of
at least 24 characters for the dashboard, stored in a password manager.

```bash
sudo -v
sudo bash <<'SETUP'
set +x
set -euo pipefail
umask 077
dir=/etc/crypto-control-mobile
install -d -m 0700 "$dir"
tmp=$(mktemp -d "$dir/.setup.XXXXXXXX")
trap 'rm -f -- "$tmp"/*.cred; rmdir -- "$tmp"' EXIT
for name in mobile_password whitebit_demo_api_key whitebit_demo_api_secret; do
    IFS= read -r -s -p "$name (hidden): " value </dev/tty
    printf '\n' >/dev/tty
    test -n "$value"
    if [[ "$name" = mobile_password ]]; then
        [[ "$value" =~ ^[[:graph:]]{24,}$ ]]
    fi
    printf '%s' "$value" | systemd-creds encrypt --with-key=host --name="$name" - "$tmp/$name.cred"
    unset value
done
for name in mobile_password whitebit_demo_api_key whitebit_demo_api_secret; do
    mv -f -- "$tmp/$name.cred" "$dir/$name.cred"
done
SETUP
sudo systemctl enable --now crypto-control-mobile.service
```

The browser login is `admin` plus the independent dashboard password.
Use a private HTTPS ingress for phone access. The proposed ingress is
Tailscale Serve with the Ubuntu server and phone joined to a private tailnet
and access granted only to the owner's account/device. After separate
authorization, configure tailnet HTTPS and run
`sudo tailscale serve --bg http://127.0.0.1:18091`. This does not use Funnel.
Confirm the authenticated dashboard and its live WhiteBIT data over mobile
data on the actual phone before marking phone access complete.

To stop the panel, run `sudo systemctl disable --now crypto-control-mobile.service`.
If Serve was configured for this panel on port 443, remove that rule with
`sudo tailscale serve --https=443 off`.
