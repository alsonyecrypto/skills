---
name: find-prediction-market
description: Use when you need to find a live Polymarket prediction market that will actually survive a real order — screening on negRisk, fee schedule, order-book depth and the share floor rather than just "is it open". Also use to check whether a market id you already have has gone stale (closed, de-listed, fees switched off), or to look a market up by name. Wraps the public Gamma + CLOB APIs; no auth needed.
---

# Find Prediction Market

Picks a Polymarket market that survives a real order, and tells you why the ones
it rejected fail. Everything runs against `gamma-api.polymarket.com` and
`clob.polymarket.com`, which are public and read-only — no key, no session, just
network access.

## "Live" is not the bar

A market can be open, accepting orders, and still be useless: the order gets
rejected, or fills identically to a market with no fees, or can't be sized small
enough to test with. The screen gates on the properties that actually cost
debugging time.

| Gate | Why it matters |
|------|----------------|
| `negRisk === false` | Multi-outcome markets settle through the NegRiskAdapter. A client signing against the plain exchange domain gets its signature rejected, and it surfaces as an **opaque 502** with nothing in it naming negRisk. Separately, the adapter needs its own allowance — a missing one returns `400 allowance: 0`, which reads exactly like an empty wallet. |
| `feesEnabled` | Without it the taker-fee path never runs, so gross and net shares come out identical and fee bugs stay invisible. `feeSchedule.rate` carries the number (0.04 `politics_fees`, 0.05 `sports_fees_v3`, 0.07 `crypto_fees_v2`). |
| `stake / bestAsk >= orderMinSize` | `orderMinSize` (usually 5 shares) floors a **limit** order, so a market priced near 0.5 can't be bought with a $2 stake. It does **not** bind a market order, which is dollar-floored near $1 instead — don't cite the share floor for one. |
| `bestAsk >= 0.10` | Dust-priced longshots pass every other gate and win on liquidity, but they tick in tenth-cents and put the taker fee near zero. |
| ask-side depth | A thin ladder makes a fill-or-kill order fail to fill rather than testing anything. |
| far-out `endDate` | So the pick doesn't rot. Default floor is 90 days. |

**`liquidityNum` is not depth.** It counts *both* sides of the book, so it says
nothing about whether a **buy** fills. It's used only to rank candidates; the
script then walks the real CLOB asks for the top few.

## Usage

Stdlib Python, no dependencies:

```bash
python3 scripts/find_markets.py                    # ranked tradable candidates
python3 scripts/find_markets.py --search "premier league"
python3 scripts/find_markets.py --check <conditionId|marketId|slug>
```

| Flag | Default | Use |
|------|---------|-----|
| `--stake N` | 2 | USD stake the share-floor check assumes. Raise it and pricier markets qualify — the ask ceiling is `stake / orderMinSize`. |
| `--min-days N` | 90 | Lower it if nothing passes. |
| `--min-ask P` | 0.10 | Lower it to allow cheaper longshots. |
| `--top N` | 8 | How many candidates get a live book fetch. |

Each candidate prints its **`conditionId`** and **event slug**, which is what the
`prediction-deeplink` skill needs — the last line of the output is a ready
command.

### Read the depth line before committing to a pick

Ranking is by total liquidity, and a candidate can show 41 levels and
**$91 at the top level**. A small order eats the top level first, so that is the
number that decides whether it fills:

```
asks: 76 levels, $143,039 total, $2,306 at the top level     ← healthy
asks: 9 levels, $1,338 total, $6 at the top level            ← will not fill
```

### `--check` says *why* a market went bad

```
❌ NOT USABLE — 'what-will-the-price-of-bitcoin-be-on-november-4th-2020'
      active=True closed=True accepting=None negRisk=None feesEnabled=False
  → Fails on: not active / closed
```

It runs the same screen and names the first failed gate, so "this stopped
working" separates into closed, de-listed, fees switched off, or book withdrawn
— rather than just "not found".

### `--search` is a weak input, not an equal one

`/public-search` ranks on text similarity with no regard for tradability, so a
thin query comes back full of resolved fixtures. Live rows sort first and every
row states which gate it fails, rather than being silently dropped. Prefer a
condition id or slug whenever the context has one.

## Gamma API behaviour that will bite you

These are the non-obvious parts, all verified against the live API:

- **`limit` is silently capped at 100.** Send `limit=500` and step by 500 and you
  get 100 rows a page while skipping the other 400 — a scan that looks complete
  and covers a fifth of the window. Offset also 422s past ~2100, so one sweep
  reaches ~2000 markets out of tens of thousands.
- **Order server-side or you sample the oldest slice.** Since the window is a
  slice, unordered paging returns whatever has the lowest ids. `/markets` accepts
  `order=liquidityNum` and `order=volumeNum`; `/events` accepts `order=volume`
  but **422s on `volumeNum`**. The two endpoints do not share a sort vocabulary.
- **The default urllib User-Agent gets a 403.** Send a browser-like one.
- **`/events` is a wider view than `/markets`, not just a different one.** The
  list form of `/markets` never returns a market whose `active` is `false` or
  whose `archived` is `true`, on any filter combination. The same sweep finds
  thousands of resolved markets through `/events` against a handful through
  `/markets`. Treat a `/markets` sweep as a sample, never as the population.
- **`/markets` silently ignores `archived=`, `active=` and `accepting_order=`**
  and answers with its default unfiltered page. So a filtered sweep looks like it
  worked and quietly returns the wrong population — it fails open, not loud.
- **The list form hides closed markets.** `?id=12` and `?condition_ids=0x…` both
  come back `[]` for a resolved market, indistinguishable from a typo. Retry with
  `closed=true` to tell the two apart. The path form `/markets/{id}` finds it but
  **drops the embedded `events`**, so you lose the event slug.
- **The book is one mirrored ladder.** `bestBid` / `bestAsk` describe the **YES**
  token only, so `noAsk = 1 − yesBid`. Using `1 − yesAsk` gives NO's *bid* — a
  lower number that quietly understates what a NO buy costs.
- **Several fields are double-encoded JSON strings** (`outcomes`,
  `clobTokenIds`), so they need a second `json.loads`.
- **Long `/events` sweeps intermittently die on `IncompleteRead` mid-response.**
  Retry the page, or you lose every page after the first failure.

## Related

- `prediction-deeplink` — takes the `conditionId` and event slug this skill
  prints and opens the buy or sell sheet on a device.
