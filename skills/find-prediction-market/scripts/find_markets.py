#!/usr/bin/env python3
"""
Find a live, *tradable* Polymarket market — one that survives a real order.

"Live" is not the bar. A market can be open, accepting orders, and still reject
or mis-sign the order, so each candidate is screened on the properties that
actually cost debugging time:

  negRisk == false   Multi-outcome markets settle through the NegRiskAdapter, so
                     a client that signs against the plain exchange domain has
                     its signature rejected. That surfaces as an opaque 502 with
                     nothing in it naming negRisk.
  feesEnabled        Without it the taker-fee path never runs, so gross and net
                     shares come out identical and fee bugs stay invisible.
  stake/ask >= min   `orderMinSize` (usually 5 shares) floors a *limit* order — a
                     market priced near 0.5 cannot be bought with a $2 stake.
  ask-side depth     A thin ladder makes a fill-or-kill order fail to fill rather
                     than testing anything. Gamma's `liquidityNum` counts *both*
                     sides, so it says nothing about whether a buy fills; this
                     walks the real CLOB asks instead.

Gamma and the CLOB order book are public and read-only, so no auth or API key is
needed — only network access.

Usage:
  python3 find_markets.py                     # ranked tradable candidates
  python3 find_markets.py --stake 5           # size the share-floor check to $5
  python3 find_markets.py --min-days 30       # accept markets closing sooner
  python3 find_markets.py --min-ask 0.05      # allow cheaper longshots
  python3 find_markets.py --search "world cup final"   # find by name
  python3 find_markets.py --check <conditionId|marketId|slug>

Stdlib only — no dependencies.
"""
import argparse
import datetime
import json
import re
import sys
import urllib.parse
import urllib.request

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"

# Gamma silently caps `limit` at 100 however large a value you send, and 422s
# once offset passes ~2100. Asking for 500 and stepping by 500 therefore returns
# 100 rows a page and skips the other 400 — scanning a fifth of the window while
# looking like it worked.
PAGE = 100
MAX_OFFSET = 2000

CONDITION_ID_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")


def get(path, **params):
    """GET a Gamma/CLOB path. Repeats keys for list values, which Gamma requires."""
    pairs = []
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            pairs.extend((key, str(v)) for v in value)
        else:
            pairs.append((key, str(value)))
    url = path if path.startswith("http") else f"{GAMMA}{path}"
    if pairs:
        url += "?" + urllib.parse.urlencode(pairs)
    # Gamma rejects urllib's default User-Agent with a 403, so send a browser-like one.
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.load(resp)


def as_list(payload):
    if isinstance(payload, list):
        return [m for m in payload if isinstance(m, dict)]
    if isinstance(payload, dict):
        return [payload]
    return []


def days_left(end_date):
    if not end_date:
        return None
    try:
        return (datetime.date.fromisoformat(end_date[:10]) - datetime.date.today()).days
    except ValueError:
        return None


def fee_rate(market):
    return (market.get("feeSchedule") or {}).get("rate")


def event_slug(market):
    """The event slug, which the list form of /markets embeds and the path form drops."""
    events = market.get("events") or []
    if events and isinstance(events[0], dict):
        return events[0].get("slug")
    return None


def side_ask(market, side):
    """
    The ask you actually pay for `side`.

    The CLOB is one book, mirrored: `bestBid`/`bestAsk` describe the **YES** token
    only. Buying NO crosses the YES bid, so `noAsk = 1 - yesBid`. Using
    `1 - yesAsk` instead gives NO's *bid* — a different, lower number that quietly
    understates the cost.
    """
    try:
        if side == "yes":
            return float(market["bestAsk"])
        return 1.0 - float(market["bestBid"])
    except (KeyError, TypeError, ValueError):
        return None


def ask_depth(market):
    """Live ask-side ladder for the YES token: (levels, total_usd, top_level_usd)."""
    try:
        token_ids = json.loads(market.get("clobTokenIds") or "[]")
        if not token_ids:
            return None
        book = get(f"{CLOB}/book", token_id=token_ids[0])
        asks = sorted(
            ((float(a["price"]), float(a["size"])) for a in book.get("asks") or []),
            key=lambda x: x[0],
        )
        if not asks:
            return None
        total = sum(p * s for p, s in asks)
        return len(asks), total, asks[0][0] * asks[0][1]
    except Exception:  # noqa: BLE001 — depth is a bonus signal, never fatal
        return None


def screen(market, stake, min_days, min_ask):
    """Return (ok, reason). `reason` names the first failed gate, for --check."""
    if not market.get("active") or market.get("closed"):
        return False, "not active / closed"
    if not market.get("acceptingOrders"):
        return False, "not accepting orders"
    if not market.get("enableOrderBook"):
        return False, "no order book"
    if not market.get("conditionId"):
        return False, "no conditionId — never deployed to the CLOB"
    if market.get("negRisk"):
        return False, "negRisk (multi-outcome — signs against the NegRiskAdapter domain)"
    if not market.get("feesEnabled"):
        return False, "fees disabled (taker-fee path won't run)"

    remaining = days_left(market.get("endDate"))
    if remaining is None or remaining < min_days:
        return False, f"ends in {remaining}d (< {min_days}d)"

    ask = market.get("bestAsk")
    if not ask:
        return False, "no bestAsk"
    # Dust-priced longshots pass every other gate and win on liquidity, but they
    # trade in tenth-cent ticks and put the taker fee (which peaks at 0.5) near
    # zero. The *upper* bound needs no flag: the share-floor check below already
    # caps the ask at stake / orderMinSize.
    if ask < min_ask:
        return False, f"ask {ask} below the {min_ask} floor (dust-priced longshot)"
    min_size = market.get("orderMinSize") or 0
    if min_size and stake / ask < min_size:
        return False, (
            f"${stake:g} at {ask} = {stake / ask:.1f} shares, under the "
            f"{min_size}-share orderMinSize"
        )
    return True, ""


def fetch_open_markets():
    """
    Open markets, deepest book first.

    Sorted server-side on purpose: the window reaches ~2100 markets out of tens of
    thousands, so taking them in default (id) order returns whatever is oldest and
    the screen then has to find a tradable market inside that arbitrary slice.
    `order=liquidityNum` puts the deep books on page one instead.
    """
    seen = {}
    for offset in range(0, MAX_OFFSET + 1, PAGE):
        try:
            page = get(
                "/markets",
                active="true",
                closed="false",
                limit=PAGE,
                offset=offset,
                order="liquidityNum",
                ascending="false",
            )
        except Exception as exc:  # noqa: BLE001 — keep whatever we already have
            print(f"  (stopped at offset {offset}: {exc})", file=sys.stderr)
            break
        if not page:
            break
        for market in as_list(page):
            seen[market.get("id")] = market
    return list(seen.values())


def report(market, stake, with_depth=True):
    """One candidate, in the shape the deeplink skill consumes."""
    ask = market.get("bestAsk")
    remaining = days_left(market.get("endDate"))
    slug = event_slug(market)
    print(f"  {(market.get('question') or '')[:76]}")
    print(f"      conditionId = {market.get('conditionId')}")
    print(f"      event slug  = {slug or '(not embedded — /markets path form drops it)'}")
    no_ask = side_ask(market, "no")
    print(
        f"      yesAsk={ask} noAsk={round(no_ask, 4) if no_ask is not None else '—'}"
        f" | ends {(market.get('endDate') or '?')[:10]} ({remaining}d)"
        f" | fee={fee_rate(market)} ({market.get('feeType')})"
    )
    if ask:
        print(
            f"      ${stake:g} buys ~{stake / ask:.1f} shares "
            f"(orderMinSize {market.get('orderMinSize')})"
        )
    if with_depth:
        depth = ask_depth(market)
        if depth:
            levels, total, top_usd = depth
            print(
                f"      asks: {levels} levels, ${total:,.0f} total, "
                f"${top_usd:,.0f} at the top level"
            )
        else:
            print("      asks: (book unavailable)")


def print_handoff(market):
    slug = event_slug(market)
    print("\n  → open the buy sheet on this market:")
    print("     python3 ../prediction-deeplink/scripts/prediction_deeplink.py \\")
    print(f"       {market.get('conditionId')} \\")
    if slug:
        print(f"       --event {slug} \\")
    print("       --side yes --open ios")


def find(stake, min_days, min_ask, top):
    print("Scanning open Polymarket markets (public API, no auth)…")
    markets = fetch_open_markets()
    print(f"Fetched {len(markets)} markets.")

    passing = [m for m in markets if screen(m, stake, min_days, min_ask)[0]]
    # Re-sort locally so the ranking holds even if a page came back out of order or
    # a fetch broke early. `liquidityNum` counts both sides of the book, so it only
    # ranks candidates — real ask depth is verified on the top few below.
    passing.sort(key=lambda m: m.get("liquidityNum") or 0, reverse=True)

    print(
        f"\n=== TRADABLE CANDIDATES ({len(passing)} passed) ===\n"
        f"negRisk false · fees enabled · ends >= {min_days}d · ask >= {min_ask} · "
        f"${stake:g} clears orderMinSize\n"
    )
    if not passing:
        print("  none — try --min-days 30, --min-ask 0.05, or a larger --stake")
        return 1

    for market in passing[:top]:
        report(market, stake)
    print_handoff(passing[0])
    return 0


def search(query, stake, min_days, min_ask, top):
    """
    Find markets by name.

    `/public-search` ranks on text similarity with no regard for whether a market
    is tradable, so a thin query comes back full of resolved fixtures. Live rows
    are sorted first and every row says why it would fail the screen, rather than
    silently dropping the ones that do.
    """
    payload = get("/public-search", q=query, limit_per_type=10)
    events = payload.get("events", []) if isinstance(payload, dict) else []
    markets = []
    for event in events:
        for market in event.get("markets") or []:
            # The embedded market carries no `events`, so graft the parent on for
            # the slug the buy sheet wants alongside the condition id.
            market = dict(market)
            market.setdefault("events", [event])
            markets.append(market)

    if not markets:
        print(f"No market matched '{query}'.")
        print("Text search is fuzzy — try fewer words, or drop --search to scan by")
        print("trading properties instead.")
        return 1

    scored = [(m, screen(m, stake, min_days, min_ask)) for m in markets]
    scored.sort(key=lambda pair: (not pair[1][0], -(pair[0].get("liquidityNum") or 0)))

    tradable = [m for m, (ok, _) in scored if ok]
    print(f"\n=== '{query}' — {len(markets)} matches, {len(tradable)} tradable ===\n")
    for market, (ok, reason) in scored[:top]:
        print(f"  {'✅' if ok else '❌'}", end="")
        report(market, stake, with_depth=ok)
        if not ok:
            print(f"      fails on: {reason}")
        print()
    if tradable:
        print_handoff(tradable[0])
    return 0


def check(identifier, stake, min_days, min_ask):
    """
    Verify one market is still tradable.

    Condition id / numeric id / slug are all accepted. Note the list form of
    `/markets` hides closed markets — `?id=12` comes back `[]` for a resolved
    market, indistinguishable from a typo — so each query is retried with
    `closed=true` to surface it and say "closed" out loud.
    """
    if CONDITION_ID_RE.match(identifier):
        params = {"condition_ids": identifier}
    elif identifier.isdigit():
        params = {"id": identifier}
    else:
        params = {"slug": identifier}

    try:
        markets = as_list(get("/markets", **params))
        if not markets:
            markets = as_list(get("/markets", closed="true", **params))
    except Exception as exc:  # noqa: BLE001
        print(f"❌ LOOKUP FAILED for '{identifier}': {exc}")
        return 1
    if not markets:
        print(f"❌ NOT FOUND — no market for '{identifier}'")
        return 1

    market = markets[0]
    ok, reason = screen(market, stake, min_days, min_ask)
    print(f"{'✅ TRADABLE' if ok else '❌ NOT USABLE'} — '{identifier}'")
    report(market, stake)
    print(
        f"      active={market.get('active')} closed={market.get('closed')} "
        f"accepting={market.get('acceptingOrders')} negRisk={market.get('negRisk')} "
        f"feesEnabled={market.get('feesEnabled')}"
    )
    if ok:
        print_handoff(market)
    else:
        print(f"\n  → Fails on: {reason}")
        print("  → Re-run without --check to pick a fresh market.")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--stake", type=float, default=2.0,
                    help="USD stake the share-floor check assumes (default 2)")
    ap.add_argument("--min-days", type=int, default=90, dest="min_days",
                    help="only markets ending >= N days out (default 90)")
    ap.add_argument("--min-ask", type=float, default=0.10, dest="min_ask",
                    help="skip asks below this price (default 0.10)")
    ap.add_argument("--top", type=int, default=8,
                    help="candidates to print, book-checked (default 8)")
    ap.add_argument("--search", metavar="TEXT",
                    help="find markets by name instead of scanning by properties")
    ap.add_argument("--check", metavar="CONDITIONID_OR_ID_OR_SLUG",
                    help="verify one market is still tradable")
    args = ap.parse_args()

    if args.check:
        return check(args.check, args.stake, args.min_days, args.min_ask)
    if args.search:
        return search(args.search, args.stake, args.min_days, args.min_ask, args.top)
    return find(args.stake, args.min_days, args.min_ask, args.top)


if __name__ == "__main__":
    sys.exit(main())
