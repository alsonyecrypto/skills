# skills

Claude Code skills I use for prediction-market work. Two of them, and they're
designed to hand off to each other.

| Skill | Job |
|---|---|
| [`find-prediction-market`](skills/find-prediction-market) | Find a live Polymarket market that survives a real order — screening on negRisk, fee schedule, book depth and the share floor, not just "is it open". Also checks whether a market id you already have has gone stale. |
| [`prediction-deeplink`](skills/prediction-deeplink) | Open that market's Buy or Sell bottom sheet straight on a simulator, or just print the deeplink URL. |

Both are stdlib Python against the public, read-only Polymarket APIs
(`gamma-api.polymarket.com`, `clob.polymarket.com`) — no key, no session, no
dependencies.

## Install

Skills live in `~/.claude/skills/`. Symlink so a `git pull` updates them:

```bash
git clone https://github.com/alsonyecrypto/skills.git ~/skills
ln -s ~/skills/skills/find-prediction-market ~/.claude/skills/find-prediction-market
ln -s ~/skills/skills/prediction-deeplink   ~/.claude/skills/prediction-deeplink
```

Or copy the directories if you'd rather pin a version.

## The handoff

Find a market worth testing:

```bash
$ python3 ~/skills/skills/find-prediction-market/scripts/find_markets.py --top 1
Scanning open Polymarket markets (public API, no auth)…
Fetched 2055 markets.

=== TRADABLE CANDIDATES (14 passed) ===
negRisk false · fees enabled · ends >= 90d · ask >= 0.1 · $2 clears orderMinSize

  Clarity Act (H.R.3633) signed into law in 2026?
      conditionId = 0x9cb23d04b2ded06147482076688b69b487a8d982c63ebdda2ab3678cf27cf390
      event slug  = clarity-act-signed-into-law-in-2026
      yesAsk=0.24 noAsk=0.77 | ends 2027-01-01 (134d) | fee=0.04 (politics_fees)
      $2 buys ~8.3 shares (orderMinSize 5)
      asks: 76 levels, $143,039 total, $2,306 at the top level
```

Then open it:

```bash
$ python3 ~/skills/skills/prediction-deeplink/scripts/prediction_deeplink.py \
    0x9cb23d04b2ded06147482076688b69b487a8d982c63ebdda2ab3678cf27cf390 \
    --side no --open ios

Clarity Act (H.R.3633) signed into law in 2026?
    · NO ask 0.7700 (gamma yes-side: bestAsk 0.24 / bestBid 0.23)
    · the buy CTA should read ~77% for NO — that is the positive tell the args arrived. A right header with a wrong percent means a stale mount
    · fees on (politics_fees, rate 0.04)
    · ends 2027-01-01T05:00:00Z

dfw://cronos/reactnative/bottomsheet?conditionId=0x9cb23d04b2ded06147482076688b69b487a8d982c63ebdda2ab3678cf27cf390&side=no&eventId=clarity-act-signed-into-law-in-2026&moduleName=PredictionMarketSheet
```

`find_markets.py` prints that second command for you as its last line.

## What these are trimmed down from

Both started as much larger internal skills. The parts that were specific to one
app's source tree — Python ports of TypeScript label resolvers, render-case
matrices keyed to component files, a ~170-page deeplink registry — are gone,
because they're worthless outside that repo.

What's left is the part that generalises: the screening rules, and the Polymarket
API behaviour that's expensive to discover. A sample from
[`find-prediction-market/SKILL.md`](skills/find-prediction-market/SKILL.md):

- `limit` is silently capped at 100. Send `limit=500`, step by 500, and you scan a
  fifth of the window while looking complete.
- `/markets` silently ignores `archived=`, `active=` and `accepting_order=` and
  answers with its default unfiltered page. It fails open, not loud.
- `/events` is a *wider* view than `/markets`, not just a different one — the list
  form of `/markets` never returns `active: false` or `archived: true` rows at all.
- The book is one mirrored ladder: `bestBid`/`bestAsk` are the **YES** token only,
  so `noAsk = 1 − yesBid`. Using `1 − yesAsk` gives NO's *bid* instead.

## Note on the deeplink scheme

`prediction-deeplink` builds `dfw://cronos/...` URLs, which target a specific
React Native host app. The URL shape, the page keys and the argument contracts are
all in the skill, so adapting it to another RN app is mostly a matter of swapping
the scheme and the two page keys in `SHEETS`.
