#!/usr/bin/env python3
"""
Build — and optionally fire — the deeplink that opens a prediction market's
Buy or Sell bottom sheet in the app.

    dfw://cronos/reactnative/bottomsheet?<args>&moduleName=<PageKey>

Two sheets are supported:

  buy    PredictionMarketSheet   keyed by CTF conditionId (+ side, + event slug)
  sell   SellJourneySheet        keyed by event slug (+ optional assetId)

TARGET is a condition id (`0x` + 64 hex) or a slug. One Gamma lookup fills in
whatever you didn't pass — the event slug for a condition id, the condition id
for a market slug — and reports whether the market is still worth opening. Pass
`--no-lookup` to skip the network entirely and just print the URL.

Usage:
  python3 prediction_deeplink.py 0xabc…def --side no --open ios
  python3 prediction_deeplink.py 0xabc…def --event fed-rate-hike-by --json
  python3 prediction_deeplink.py my-event-slug --sheet sell --open ios
  python3 prediction_deeplink.py 0xabc…def --no-lookup

Stdlib only — no dependencies.
"""
import argparse
import json
import re
import shlex
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

GAMMA = "https://gamma-api.polymarket.com"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"

CONDITION_ID_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")

# The ST build registers its own scheme; UAT and prod share `dfw`.
SCHEME_BY_VARIANT = {"uat": "dfw", "prod": "dfw", "st": "dfw-st"}

SHEETS = {
    "buy": "PredictionMarketSheet",
    "sell": "SellJourneySheet",
}


# ── Gamma ─────────────────────────────────────────────────────────────────────


def get(path, **params):
    pairs = [(k, str(v)) for k, v in params.items() if v is not None]
    url = f"{GAMMA}{path}" + (f"?{urllib.parse.urlencode(pairs)}" if pairs else "")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        body = exc.read()[:300].decode(errors="replace")
        raise SystemExit(f"Gamma {exc.code} on {url}\n{body}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"Cannot reach Gamma ({exc.reason}). Use --no-lookup to skip it.")


def as_list(payload):
    if isinstance(payload, list):
        return [m for m in payload if isinstance(m, dict)]
    if isinstance(payload, dict):
        return [payload]
    return []


def query_markets(**params):
    """
    Query /markets, retrying once with closed=true.

    The list form hides closed markets: `?condition_ids=0x…` comes back `[]` for a
    resolved market, which is indistinguishable from a typo. The retry surfaces it
    so `verify` can say "closed" out loud. The path form `/markets/{id}` would also
    find it, but drops the embedded `events` the event slug comes from.
    """
    markets = as_list(get("/markets", **params))
    return markets or as_list(get("/markets", closed="true", **params))


def event_slug(market):
    events = market.get("events") or []
    if events and isinstance(events[0], dict):
        return events[0].get("slug")
    return None


def lookup(target, need_market):
    """
    Resolve TARGET to (market, event_slug). Either may be None.

    `need_market` is the sheet's requirement, not a preference: the buy sheet is
    keyed by one conditionId, so an event holding twenty markets is ambiguous and
    the caller has to pick. The sell sheet keys off the event slug alone, so the
    same event is a perfectly ordinary input there.
    """
    if CONDITION_ID_RE.match(target):
        markets = query_markets(condition_ids=target)
        if not markets:
            raise SystemExit(
                f"Gamma knows no market with conditionId {target}.\n"
                "If the id is right but de-listed, pass --event <slug> --no-lookup."
            )
        return markets[0], event_slug(markets[0])

    # A slug: could name an event or a single market.
    events = as_list(get("/events", slug=target))
    if events:
        markets = [m for m in (events[0].get("markets") or []) if isinstance(m, dict)]
        if need_market and len(markets) > 1:
            names = "\n".join(
                f"  {m.get('conditionId')}  {(m.get('groupItemTitle') or m.get('question') or '')[:60]}"
                for m in markets[:20]
            )
            raise SystemExit(
                f"Event '{target}' holds {len(markets)} markets — pass one condition id "
                f"as TARGET instead:\n{names}"
            )
        return (markets[0] if len(markets) == 1 else None), target

    markets = query_markets(slug=target)
    if markets:
        return markets[0], event_slug(markets[0])
    raise SystemExit(f"Gamma knows no event or market with slug '{target}'.")


# ── Verification ──────────────────────────────────────────────────────────────


def side_ask(market, side):
    """
    The ask you actually pay for `side`.

    The CLOB is one mirrored book: `bestBid`/`bestAsk` describe the **YES** token
    only. Buying NO crosses the YES bid, so `noAsk = 1 - yesBid`. Using
    `1 - yesAsk` gives NO's *bid* — a lower number that understates the cost.
    """
    try:
        if side == "yes":
            return float(market["bestAsk"])
        return 1.0 - float(market["bestBid"])
    except (KeyError, TypeError, ValueError):
        return None


def verify(market, side):
    """Split findings into blockers (sheet opens but is useless) and notes."""
    blockers, notes = [], []

    if not market.get("conditionId"):
        blockers.append("no conditionId — never deployed to the CLOB, so it has no tradable side")
    if market.get("closed"):
        blockers.append("closed")
    if not market.get("active", True):
        blockers.append("inactive")
    if not market.get("acceptingOrders", True):
        blockers.append("acceptingOrders=false — the sheet opens but every order is rejected")

    ask, bid = market.get("bestAsk"), market.get("bestBid")
    try:
        if float(ask) >= 1.0 and float(bid or 0) <= 0.0:
            blockers.append(
                "ask 1 / bid 0 — no book on either side. Usually a placeholder outcome "
                "('Team A', 'Other') held open until the field is known"
            )
    except (TypeError, ValueError):
        pass

    paying = side_ask(market, side)
    if paying:
        notes.append(f"{side.upper()} ask {paying:.4f} (gamma yes-side: bestAsk {ask} / bestBid {bid})")
        notes.append(
            f"the buy CTA should read ~{round(paying * 100)}% for {side.upper()} — that is the "
            "positive tell the args arrived. A right header with a wrong percent means a stale mount"
        )
    else:
        notes.append(f"no usable {side.upper()} ask — a buy has nothing to fill against")

    if market.get("negRisk"):
        notes.append(
            "negRisk=true — multi-outcome. Needs the NegRiskAdapter allowance on the deposit "
            "wallet, or submit 400s with 'allowance: 0', which reads like an empty wallet"
        )
    if not market.get("feesEnabled"):
        notes.append("feesEnabled=false — the taker-fee path never runs, so fee bugs stay invisible")
    else:
        notes.append(f"fees on ({market.get('feeType')}, rate {(market.get('feeSchedule') or {}).get('rate')})")
    if not market.get("image"):
        notes.append("no image — the buy header renders a blank avatar")
    if market.get("endDate"):
        notes.append(f"ends {market['endDate']}")
    return blockers, notes


# ── Deeplink ──────────────────────────────────────────────────────────────────


def build(*, sheet, condition_id, slug, asset_id, side, show_back, variant):
    query = []
    if sheet == "buy":
        if not condition_id:
            raise SystemExit("The buy sheet needs a condition id. Pass one as TARGET.")
        query.append(("conditionId", condition_id))
        query.append(("side", side))
        if slug:
            # Send it whenever known: a de-listed or knocked-out market is
            # unreachable by conditionId alone, because the /markets list form
            # withholds active=false / archived=true rows. eventId unlocks the
            # /events path the sheet falls back to.
            query.append(("eventId", slug))
        if show_back:
            # Literal 'true' only. Every value is String()-serialised into the
            # query, so a boolean false would arrive as the truthy string 'false'.
            query.append(("showBackStr", "true"))
    else:
        if not slug:
            raise SystemExit(
                "The sell sheet is keyed by event slug. Pass the slug as TARGET, or "
                "--event <slug> alongside a condition id."
            )
        query.append(("eventId", slug))
        if asset_id:
            # Selects which holding to open; omitted, the sheet falls back to the
            # first position returned for the wallet.
            query.append(("assetId", asset_id))

    query.append(("moduleName", SHEETS[sheet]))
    encoded = "&".join(
        f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}" for k, v in query
    )
    scheme = SCHEME_BY_VARIANT[variant]
    return f"{scheme}://cronos/reactnative/bottomsheet?{encoded}"


# ── Firing ────────────────────────────────────────────────────────────────────


def booted_ios_devices():
    try:
        raw = subprocess.run(
            ["xcrun", "simctl", "list", "devices", "booted", "-j"],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout
    except (subprocess.SubprocessError, FileNotFoundError):
        return []
    return [
        (d.get("udid", ""), d.get("name", "?"))
        for group in (json.loads(raw).get("devices") or {}).values()
        for d in group
        if d.get("state") == "Booted"
    ]


def fire(url, platform, device, package):
    if platform == "ios":
        if not device:
            booted = booted_ios_devices()
            if not booted:
                raise SystemExit("No booted simulator. Boot one, then re-run with --open ios.")
            if len(booted) > 1:
                listing = "\n".join(f"  {u}  {n}" for u, n in booted)
                raise SystemExit(f"Several simulators booted — pass --device UDID:\n{listing}")
            device = booted[0][0]
        cmd = ["xcrun", "simctl", "openurl", device, url]
    else:
        # The URL crosses the device's shell, where a bare & would background the
        # command and silently truncate every arg after the first.
        cmd = ["adb"] + (["-s", device] if device else [])
        cmd += ["shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", f"'{url}'", package]

    print(f"\n$ {shlex.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"Failed:\n{result.stdout}{result.stderr}")
    print((result.stdout or "opened").strip())
    print(
        "\nNOTE: openurl exits 0 even when the deeplink is dropped. Screenshot to confirm:\n"
        f"  sleep 4 && xcrun simctl io {device or '<UDID>'} screenshot /tmp/sheet.png"
    )


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("target", help="condition id (0x…64 hex) or event/market slug")
    ap.add_argument("--sheet", choices=sorted(SHEETS), default="buy")
    ap.add_argument("--side", choices=["yes", "no"], default="yes",
                    help="which outcome the buy sheet opens on (default yes)")
    ap.add_argument("--event", dest="slug", help="event slug, if TARGET is a condition id")
    ap.add_argument("--asset-id", dest="asset_id", help="sell sheet: which holding to open")
    ap.add_argument("--back", action="store_true",
                    help="add showBackStr=true — only when opening from another sheet")
    ap.add_argument("--variant", choices=sorted(SCHEME_BY_VARIANT), default="uat",
                    help="build variant; st switches the scheme to dfw-st (default uat)")
    ap.add_argument("--no-lookup", action="store_true",
                    help="skip Gamma entirely — just print the URL")
    ap.add_argument("--open", dest="platform", choices=["ios", "android"],
                    help="fire it at a booted simulator / connected device")
    ap.add_argument("--device", help="simulator UDID or adb serial")
    ap.add_argument("--package", default="com.defi.wallet", help="Android package")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    condition_id = args.target if CONDITION_ID_RE.match(args.target) else None
    slug = args.slug
    market = None

    if args.no_lookup:
        if not condition_id and not slug:
            slug = args.target
    else:
        market, found_slug = lookup(args.target, need_market=args.sheet == "buy")
        slug = slug or found_slug
        condition_id = condition_id or (market or {}).get("conditionId")

    url = build(
        sheet=args.sheet,
        condition_id=condition_id,
        slug=slug,
        asset_id=args.asset_id,
        side=args.side,
        show_back=args.back,
        variant=args.variant,
    )

    if args.json:
        print(json.dumps({
            "url": url,
            "sheet": SHEETS[args.sheet],
            "conditionId": condition_id,
            "eventId": slug,
            "side": args.side if args.sheet == "buy" else None,
            "question": (market or {}).get("question"),
        }, indent=2))
    else:
        if market:
            print(f"\n{market.get('question')}")
            blockers, notes = verify(market, args.side)
            if blockers:
                print("\n  BLOCKERS — the sheet opens, but every order dies:")
                for item in blockers:
                    print(f"    ✗ {item}")
            for item in notes:
                print(f"    · {item}")
        print(f"\n{url}")

    if args.platform:
        if not args.no_lookup and market:
            blockers, _ = verify(market, args.side)
            if blockers:
                print("\n(firing anyway — blockers above are about ordering, not rendering)")
        fire(url, args.platform, args.device, args.package)
    return 0


if __name__ == "__main__":
    sys.exit(main())
