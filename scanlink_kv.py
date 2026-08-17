#!/usr/bin/env python3
"""
Fast scanlink-only refresh for the online site.

Re-extracts the (rotating, ~few-hour-lived) Chartink scanlink for the weekly and daily
screeners — WITHOUT downloading any backtest — and writes the fresh hashes into the
Cloudflare KV namespace SCANLINKS. The deployed pages read them via /api/scanlink on load
and patch their in-scan ticker links, so links keep working between full scrapes. Runs in
the "scanlink" GitHub workflow (dispatched by the ⚡ Links button); ~1 min, no redeploy.

Env: CF_ACCOUNT, CF_KV_NS, CLOUDFLARE_API_TOKEN (needs Workers KV Storage: Edit).
"""
import json
import os
import sys
import urllib.request
from datetime import datetime

from scrape import scanlink_only, polite_pause

SCREENERS = {
    "weekly": "https://chartink.com/screener/cp-ich-trend-bounce-wkly",
    "daily": "https://chartink.com/screener/cp-ich-trend-bounce-dly",
}


def kv_put(account: str, ns: str, token: str, key: str, value: str) -> None:
    url = f"https://api.cloudflare.com/client/v4/accounts/{account}/storage/kv/namespaces/{ns}/values/{key}"
    req = urllib.request.Request(
        url,
        data=value.encode("utf-8"),
        method="PUT",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "text/plain"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.loads(r.read().decode("utf-8", "replace"))
    if not body.get("success"):
        raise RuntimeError(f"KV PUT {key} failed: {body}")
    print(f"✔ KV {key} = {value}")


def main() -> int:
    account = os.environ.get("CF_ACCOUNT", "").strip()
    ns = os.environ.get("CF_KV_NS", "").strip()
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not (account and ns and token):
        print("✖ need CF_ACCOUNT, CF_KV_NS, CLOUDFLARE_API_TOKEN", file=sys.stderr)
        return 2

    fresh = {}
    for i, (key, url) in enumerate(SCREENERS.items()):
        if i:
            polite_pause(30.0)  # anti-bot: random gap between pulls
        res = scanlink_only(url)
        link = (res or {}).get("scanlink")
        if not link:
            print(f"⚠ {key}: no scanlink extracted; leaving KV unchanged", file=sys.stderr)
            continue
        fresh[key] = link

    if not fresh:
        print("✖ no scanlinks extracted", file=sys.stderr)
        return 1

    for key, link in fresh.items():
        kv_put(account, ns, token, key, link)
    kv_put(account, ns, token, "updated_at", datetime.now().strftime("%Y-%m-%d %H:%M"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
