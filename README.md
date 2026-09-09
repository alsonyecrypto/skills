# skills

Claude Code skills I use, in two pairs. Each pair is designed to hand off
internally — though one half of the prediction pair has expired, marked below.

| Skill | Job |
|---|---|
| [`find-prediction-market`](skills/find-prediction-market) | Find a live Polymarket market that survives a real order — screening on negRisk, fee schedule, book depth and the share floor, not just "is it open". Also checks whether a market id you already have has gone stale. |
| [`prediction-deeplink`](skills/prediction-deeplink) | **⚠️ Expired.** Opened that market's Buy or Sell bottom sheet straight on a simulator. It has drifted from the app build it targets — kept as a reference for the deeplink URL anatomy, not as a working tool. |
| [`context-to-tickets`](skills/context-to-tickets) | Turn a Slack thread (or a pile of pasted context) into a reviewed set of Jira tickets under a parent epic — split into separate issues and research items, deduped against what's already filed, and held at an approval gate before anything is written. |
| [`jira-ticket`](skills/jira-ticket) | File one well-formed ticket: platform-tagged title, structured description for whoever does the fix, and a short human-read first comment with screenshots embedded inline. |

The prediction pair is stdlib Python against the public, read-only Polymarket APIs
(`gamma-api.polymarket.com`, `clob.polymarket.com`) — no key, no session, no
dependencies.

The Jira pair runs on the Slack and Atlassian MCP servers, plus one
Chrome/AppleScript helper for the two things the Atlassian MCP cannot do at all:
upload a file, and embed an image inline in a comment.

## Install

Skills live in `~/.claude/skills/`. Symlink so a `git pull` updates them:

```bash
git clone https://github.com/alsonyecrypto/skills.git ~/skills
ln -s ~/skills/skills/find-prediction-market ~/.claude/skills/find-prediction-market
ln -s ~/skills/skills/context-to-tickets     ~/.claude/skills/context-to-tickets
ln -s ~/skills/skills/jira-ticket            ~/.claude/skills/jira-ticket
```

`prediction-deeplink` is deliberately not in that list — it is expired. Read it in
the repo if you want the URL anatomy; don't install it.

Or copy the directories if you'd rather pin a version.

The Jira pair additionally needs the Slack + Atlassian MCP servers connected, and
for attachments: `JIRA_HOST=your-site.atlassian.net`, macOS, and Chrome signed in
to Jira with **View > Developer > Allow JavaScript from Apple Events** enabled.

## The prediction handoff

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

Then open it — **this half of the handoff is expired.** `prediction-deeplink` is
retired; the URL shape it documents still matches a newer private version:

```
dfw://cronos/reactnative/bottomsheet?conditionId=0x9cb23d04…&side=no&eventId=clarity-act-signed-into-law-in-2026&moduleName=PredictionMarketSheet
```

…but it is missing a sheet that now exists in this family, and it is no longer
verified against the app. `find_markets.py` still prints a ready command as its
last line — read it for the argument shape, and check the URL against the app
before firing it.

## The Jira handoff

Hand `context-to-tickets` a Slack thread link plus a parent epic. It reads every
source, splits the discussion into one item per independently closable thing,
classifies each as a defect or a pending-research item, checks them against what
is already filed under that parent, and then **stops** and shows a triage table:

```
Parent: PROJ-100 · QA Tracking (Epic, project PROJ)
Convention detected: `[Issue] <summary>`, type Task, title-only bodies

| # | Kind     | Type | Proposed title | Dup                          | Proposed action                             |
|---|----------|------|----------------|------------------------------|---------------------------------------------|
| 1 | issue    | Task | [Issue] …      | DUP → PROJ-104 (empty shell) | enrich PROJ-104 (fill description, retitle)  |
| 2 | research | Task | [Issue] …      | NEW                          | create                                       |
```

Nothing is written until you pick rows. Duplicates are never resolved silently —
an existing ticket that is just a title someone filed in a hurry gets *enriched*
rather than twinned, and that choice is always yours. `jira-ticket` is the
single-ticket layer underneath, for when you already know exactly what one ticket
should say.

## What's expensive to discover

The point of keeping these as skills is the behaviour that costs an afternoon to
find out. From [`find-prediction-market/SKILL.md`](skills/find-prediction-market/SKILL.md):

- `limit` is silently capped at 100. Send `limit=500`, step by 500, and you scan a
  fifth of the window while looking complete.
- `/markets` silently ignores `archived=`, `active=` and `accepting_order=` and
  answers with its default unfiltered page. It fails open, not loud.
- `/events` is a *wider* view than `/markets`, not just a different one — the list
  form of `/markets` never returns `active: false` or `archived: true` rows at all.
- The book is one mirrored ladder: `bestBid`/`bestAsk` are the **YES** token only,
  so `noAsk = 1 − yesBid`. Using `1 − yesAsk` gives NO's *bid* instead.

And from the Jira pair:

- **Markdown → ADF drops illegal nesting silently and still returns `200`.** A
  blockquote under a list item is invalid ADF, so the converter discards *the whole
  list item* — not just the quote. Keep list items inline-only and read the
  description back after every write.
- **`slack_read_file` refuses video by type, not size.** A 2.6 MB mp4 is rejected
  outright; there is no flag for it. A screen recording can only ever be a link in
  the ticket unless you download it and pull keyframes yourself.
- **Jira hierarchy is by level, not by name.** Only `hierarchyLevel: 0` types can
  child an Epic at level 1, sub-task types sit at `-1`, and some projects define
  custom types at level `2` — *above* Epic. So two level-0 types can never parent
  each other: "put these under a Story" necessarily means sub-tasks.
- **Inline images need the media-services file UUID, not the attachment id.** The
  numeric id is rejected by the comment ADF validator. The UUID only shows up in
  the final redirect of `/rest/api/3/attachment/content/{id}`.

## What these are trimmed down from

All four started as larger internal skills. The parts specific to one app's source
tree or one company's Jira — Python ports of TypeScript label resolvers,
render-case matrices keyed to component files, a ~170-page deeplink registry, a
hardcoded Jira host, one project's issue-type inventory, internal channel ids —
are gone, because they're worthless outside the place they came from.

What's left is the part that generalises: the screening rules, the workflow, and
the API behaviour above.

## Note on the deeplink scheme

`prediction-deeplink` builds `dfw://cronos/...` URLs, which target a specific
React Native host app. The URL shape, the page keys and the argument contracts are
all still written up in the skill, so adapting it to another RN app — or catching
it back up — is mostly a matter of swapping the scheme and the page keys in
`SHEETS`.
