---
name: prediction-deeplink
description: EXPIRED — do not use. This skill has drifted from the app build it targets and is kept only as a historical reference for the deeplink URL anatomy. Do not load it to open a sheet on a device, to build a deeplink URL, or to reproduce a bug on a market — the script emits URLs that are wrong on at least one current build variant. There is no maintained public replacement.
---

# Prediction Deeplink — EXPIRED

> **⚠️ EXPIRED — do not rely on this skill.**
>
> Retired by the maintainer. Treat it as a historical reference, not a working tool.
>
> A spot-check against a newer private version found most of this still accurate:
> the `dfw://cronos/reactnative/bottomsheet` shape, the per-build-variant scheme
> handling, and the `PredictionMarketSheet` / `SellJourneySheet` keys with the
> argument contracts documented below all still match. The one concrete gap found is
> a third sheet in this family that exists now and is missing here.
>
> That gap is narrow, so whatever retired this is probably not visible in the
> script — assume the app has moved in some way this write-up does not capture, and
> verify against the app before trusting any URL it builds.

The app routes every screen through a deeplink, so a bottom sheet can be opened
directly — no Discovery → Sports → find the card. That turns "go reproduce this
on the Fed rate market" from a navigation hunt into one command.

```
dfw://cronos/reactnative/bottomsheet?<args>&moduleName=<PageKey>
```

Two sheets:

| Sheet | Page key | Keyed by | Also takes |
|---|---|---|---|
| buy | `PredictionMarketSheet` | `conditionId` (`0x` + 64 hex) | `side`, `eventId`, `showBackStr` |
| sell | `SellJourneySheet` | `eventId` (event slug) | `assetId` |

**The buy sheet wants a CTF condition id, not a numeric market id.** Get one from
the `find-prediction-market` skill, which prints the condition id and event slug
together.

## Usage

```bash
python3 scripts/prediction_deeplink.py <TARGET> [--side yes|no] [--open ios]
```

`TARGET` is a condition id or a slug. One Gamma lookup fills in whatever you
didn't pass — the event slug for a condition id, the condition id for a market
slug — and reports whether the market is still worth opening.

```bash
# buy, NO side, fire at the booted simulator
python3 scripts/prediction_deeplink.py 0xabc…def --side no --open ios

# sell sheet for an event, first holding
python3 scripts/prediction_deeplink.py my-event-slug --sheet sell --open ios

# just the URL, no network
python3 scripts/prediction_deeplink.py 0xabc…def --event my-slug --no-lookup
```

| Flag | Notes |
|---|---|
| `--side yes\|no` | **Defaults to `yes`.** See below — this is a real decision, not a default. |
| `--sheet buy\|sell` | Default `buy`. |
| `--event SLUG` | Event slug, when `TARGET` is a condition id and you want to skip the lookup. |
| `--asset-id ID` | Sell sheet only — which holding to open. Omitted, the sheet falls back to the wallet's first position. |
| `--back` | Adds `showBackStr=true`. Only when opening *from another sheet*. |
| `--variant uat\|prod\|st` | Default `uat`. `st` switches the scheme to `dfw-st`. |
| `--open ios\|android` | Fire it. Android needs `--package`. |
| `--no-lookup` | Skip Gamma entirely. Use it when the market is de-listed (see below) or the network is out. |
| `--json` | Machine-readable output. |

### Ask which side before you fire

`--side` defaults to `yes`, so a bug report that doesn't name a side is a coin
flip — reproduce the No leg as Yes and a real failure comes back clean. If the
context doesn't say, **say so in your report** rather than letting the default
pass for a decision.

### Booleans are banned from the query

Every value is `String()`-serialised into the URL, so a boolean `false` arrives
as the truthy string `'false'`. Send the literal `'true'` or omit the key — which
is what the `showBackStr` naming convention is there to signal.

### A multi-market event behaves differently per sheet

Buy is keyed by one condition id, so an event holding twenty markets is ambiguous
and the script lists them and stops. Sell keys off the event slug alone, so the
same event is an ordinary input there.

### De-listed markets need `--event`

Gamma's `/markets` list form never returns a market whose `active` is `false` or
whose `archived` is `true`. So a de-listed or knocked-out market can't be found —
or opened — by condition id alone. Pass `--event <slug>` (which unlocks the
`/events` path the sheet falls back to) with `--no-lookup`.

## What gets checked before you burn a run

Findings split into two piles, because "unusable" and "worth knowing" deserve
different reactions:

- **Blockers** — `closed`, `active: false`, `acceptingOrders: false`, no
  `conditionId`, or ask 1 / bid 0. The sheet still opens; every order dies. Say so
  rather than letting someone debug a resolved market for twenty minutes.
- **Notes** — `negRisk`, fee schedule, the side's ask, a missing image, the end
  date. None should stop you, and one of them is often the entire point of the test.

`negRisk: true` earns a specific callout: it needs the NegRiskAdapter allowance on
the deposit wallet, or submit returns `400 allowance: 0`. That failure reads like
an empty wallet and gets misfiled as a funding problem.

## Firing it

The app must already be **running** — a deeplink into a cold app can land before
JS is ready and native drops it silently.

```bash
xcrun simctl list devices booted
xcrun simctl launch <UDID> com.defi.wallet && sleep 6
```

All iOS variants share the bundle id `com.defi.wallet`, which cuts both ways: a
Stg install silently replaces a UAT one, session included.

## Confirm what rendered — don't assume

**`simctl openurl` exits 0 even when the deeplink is dropped.** The exit code and
the word `opened` carry no signal. A screenshot is the only evidence:

```bash
sleep 4   # the sheet fetches before it can render
xcrun simctl io <UDID> screenshot /tmp/sheet.png
```

Three things to read off it, in order:

1. **Header** — event title over the outcome name. A wrong market is obvious.
2. **The CTA percent must match the ask the script printed for that side.** This is
   the positive tell that the args actually arrived — a right header with a wrong
   percent means a stale mount. Note the CTA does *not* track `bestAsk` on a NO
   run: the book is mirrored, so `noAsk = 1 − yesBid`.
3. **The `Available` row** — the wallet balance, which exists nowhere but the
   screen. A UAT wallet holding a few dollars can't reach the sheet's default
   preset, so the CTA sits disabled and it reads like a broken build.

### Two failure modes that only show up here

- **A Metro reload silently drops the deeplink args.** Hitting reload leaves the
  sheet on screen but re-mounts it *without its props*, so it renders a different
  market and looks perfectly healthy. After any reload, re-fire the URL.
- **A second deeplink's effect depends on the pair.** Same page key → re-renders
  in place with the new args. *Different* page key → **stacks on top**, first sheet
  still mounted and dimmed behind it. So swiping the top one away reveals the stale
  one instead of the app. Fire whatever must remain on screen **last**, and say so
  when you hand the device over.

If the screenshot shows something unrelated, don't re-fire blindly — check nothing
else is driving the device, then fire a known-good target to prove the pipeline
works before concluding your URL is wrong.

## Stop at the loaded sheet

Entering an amount and tapping Buy spends real funds and is the human's call. Hand
over what to tap instead, and name a concrete stake that clears the order floor and
fits inside `Available` — the default preset usually doesn't.

## Related

- `find-prediction-market` — when you have no market yet and need one that survives
  a real order. This skill opens a market you already know; that one finds you one
  worth opening.
