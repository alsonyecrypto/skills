---
name: context-to-tickets
description: Turn one or more raw context sources — a Slack thread or channel window, a Slack file/screen recording, a pasted chat log, or the current conversation — into a reviewed set of Jira tickets filed under a given parent epic. Summarizes the context, splits it into separate issues and pending-research items, checks them against the parent's existing children for duplicates, and on a duplicate pauses to ask whether to enrich the existing ticket (filling in a title-only shell), comment on it, or file a new one. Shows a triage table for approval before any write. Use this whenever the user supplies discussion context (especially Slack links) plus a parent ticket/epic and asks to "create tickets from this", "file these issues under PROJ-123", "summarize this thread into tickets", "log the issues from this discussion", or hands off a bug-bash / QA thread for tracking. For a single ticket whose content the user already dictated, use jira-ticket instead.
---

# Context → Jira tickets

One Slack thread usually holds several separate things: a reproducible defect, a
regression nobody has bisected yet, an unreproducible report, and a "someone go
read the code" item. This skill reads the context, splits it into one ticket per
independently actionable thing, and files them under a parent epic.

Three hard rules:

1. **Nothing is written to Jira before the user approves the triage table.**
   Deleting mis-filed tickets is annoying; a 30-second review is not.
2. **A duplicate is never resolved silently.** Enrich the existing ticket, comment
   on it, or file a new one — the choice belongs to the user, so stop and ask
   (step 5).
3. **Every ticket links back to the exact Slack message it came from.** A
   tracking ticket whose provenance is lost is worse than no ticket.

Single ticket, content already dictated by the user → use **`jira-ticket`**. This
skill is the fan-out layer above it and reuses its formatting and its attachment
script.

## Setup

- **Slack MCP** and **Atlassian MCP** both connected. Without either, this skill
  cannot take its first step, and the failure is vague — check first.
- The **`jira-ticket`** skill installed alongside this one, if you need
  attachments or inline images.

## Tools

| Need | Use |
| --- | --- |
| Read Slack threads / channels / users | Slack MCP (`slack_read_thread`, `slack_read_channel`, `slack_read_file`) |
| Read / create / edit Jira issues, text comments | Atlassian MCP (`getJiraIssue`, `searchJiraIssuesUsingJql`, `getJiraProjectIssueTypesMetadata`, `createJiraIssue`, `editJiraIssue`, `addCommentToJiraIssue`) |
| Upload attachments, inline images in comments | `~/.claude/skills/jira-ticket/scripts/jira_browser_ops.py` |

`cloudId` for Atlassian MCP calls is your site host, e.g.
`your-site.atlassian.net`.

## Workflow

### 1. Collect and read every source

Parse the links the user pasted (formats and gotchas → `references/sources.md`):

- **Message / thread** `…/archives/<CHANNEL>/p<TS>` → `slack_read_thread` with
  `channel_id=<CHANNEL>`, `message_ts=<TS with a decimal point before the last 6
  digits>`. `p1788879046420899` → `1788879046.420899`.
- **File** `…/files/<USER>/<FILE_ID>/<name>` → `slack_read_file(file_id=<FILE_ID>)`.
- **Pasted log / current conversation** → use as-is.
- **Local screenshots** → keep the paths for step 7.

Read them **in parallel**. Then state plainly what you could and could not read.

> **Video and audio can never be read.** `slack_read_file` rejects `video/*` on
> type, not size — a 2.6 MB mp4 is refused. Do not claim to have watched a
> recording. Either escalate per `references/sources.md` (download → `ffmpeg`
> keyframes → attach) or carry the link forward and rely on what the thread text
> says about it.

If a source turns out to hold no actionable content, say so instead of inventing
an item to justify the read.

### 2. Resolve the parent and learn its local convention

```
getJiraIssue <PARENT> fields: summary,issuetype,project
searchJiraIssuesUsingJql  jql: "parent = <PARENT> ORDER BY created DESC"
                          fields: summary,issuetype,status
getJiraProjectIssueTypesMetadata <PROJECT-KEY>
```

**Adopt the convention already in use under that parent — do not impose a
different one.** From the existing children read off:

- the **title prefix** actually used (`[Issue] …`, `[iOS 26.0] …`, none, …);
- the **issue type** actually used for this kind of item;
- how much **body** they carry (some tracking epics hold title-only tickets).

If the parent has no children yet, fall back to `jira-ticket`'s house style and
say that you did.

> **Hierarchy gotcha — read the levels, never the names.** Only issue types at
> `hierarchyLevel: 0` (typically `Story` / `Task` / `Bug`) can be a child of an
> Epic at `hierarchyLevel: 1`. Sub-task-style types sit at `-1` and can only hang
> off a level-0 issue, and projects sometimes define custom types *above* Epic at
> level `2`, which cannot be an epic's child at all. So "put these under a Story"
> means the children must be sub-task types — two level-0 types can never parent
> each other. Check `getJiraProjectIssueTypesMetadata` every time; the same type
> name is configured differently across projects.

Type mapping, unless the parent's existing children say otherwise:

| Item kind | Type |
| --- | --- |
| Reproducible defect | `Bug` |
| Pending research: unknown cause, unreproducible, regression not yet bisected, "go read the code" | `Task` |
| Agreed follow-up work / optimization | `Task` |

Many projects have no `Spike` type — check the metadata rather than assuming, and
land research items on `Task` when it is absent.

### 3. Split the context into items

One item = **one thing that can be independently owned, finished, and closed.**

Split when the parts have different owners, different root causes, or could land
in different PRs. Keep together when one is just evidence for the other.

Classify each as **`issue`** (observed defective behaviour) or **`research`**
(cause unknown, reproduction unknown, or the next step is investigation).

Do **not** raise an item for: acknowledgements, "I'll take a look", status pings,
questions already answered inside the thread, or the same complaint restated by a
second person — fold that in as evidence instead.

For each item collect: title, kind, the messages that evidence it (permalinks),
who reported it, who is already on it, and what is still unknown.

### 4. Check for duplicates

Compare each candidate against the parent's existing children (step 2) and
against recent project issues when the wording is generic:

```
searchJiraIssuesUsingJql jql: "project = <KEY> AND summary ~ \"<distinctive words>\" ORDER BY created DESC"
```

Widen to sibling projects when the same product is tracked in more than one — a
duplicate filed in another project is still a duplicate.

Tag every candidate:

- **NEW** — file it.
- **DUP → `<KEY>`** — same thing already tracked. Default to **not** filing;
  offer to add the new evidence as a comment on `<KEY>` instead.
- **EXTENDS → `<KEY>`** — related but genuinely separate. File it, and say in the
  description how it relates.

An empty-description existing ticket still counts as a duplicate — a title
someone filed in a hurry is exactly what this skill should be filling in.

### 5. Inspect each duplicate and propose a path — never pick silently

A duplicate has **three** possible resolutions, and they are not
interchangeable. Read the existing ticket before proposing one:

```
getJiraIssue <KEY> fields: summary,description,comment,attachment,labels,status,assignee
                    responseContentFormat: markdown
```

Judge whether it is an **empty shell** (`description` null or blank, no comments,
no attachments — common on tracking epics) or **substantive** (a description
and/or comments carrying real analysis).

| Existing ticket | Default proposal | Also offer |
| --- | --- | --- |
| Empty shell | **Enrich in place** — `editJiraIssue` to fill `description`, and tighten `summary` if the candidate title is more precise | comment-only · new ticket |
| Substantive, same scope | **Add a comment** with the new evidence only | enrich · new ticket |
| Substantive, but the candidate is materially broader or narrower | **Create a new ticket**, cross-linked both ways | comment · enrich |

Then **pause and ask per duplicate**, stating which of the three you recommend and
why. Batch the questions if there are several, but do not resolve any of them on
your own initiative.

Hard rules:

- **Never overwrite a non-empty `description`.** To add to one, append with the
  original text preserved verbatim, or use a comment instead.
- **Show `old → new` when proposing a `summary` change**, and keep the parent's
  title convention (step 2).
- Do not touch `status`, `assignee`, `priority`, or `labels` while enriching.
- If the ticket is closed/done, default to a new ticket rather than reopening it
  by editing.

### 6. Show the triage table and stop

Post this and **wait**. No `createJiraIssue` / `editJiraIssue` /
`addCommentToJiraIssue` before an explicit go-ahead.

```markdown
Parent: PROJ-100 · <parent epic summary> (Epic, project PROJ)
Convention detected: `[Issue] <summary>`, type Task, title-only bodies

| # | Kind | Type | Proposed title | Dup | Proposed action | Evidence |
|---|------|------|----------------|-----|-----------------|----------|
| 1 | issue    | Task | [Issue] … | DUP → PROJ-104 (empty shell) | enrich PROJ-104 (fill description, retitle) | <permalink> |
| 2 | research | Task | [Issue] … | NEW | create | <permalink> |
```

Below the table, one line per item: why it is separate, why that action, and what
you deliberately did **not** raise. Then ask which rows to act on, whether the
detected convention is right, and — for each duplicate — to confirm the proposed
resolution.

Accept edits to titles, kinds, types, actions, and row selection before
proceeding.

### 7. Execute the approved actions

**Create** (`createJiraIssue`) — `projectKey`, `parent: <PARENT-KEY>`,
`issueTypeName`, `summary`, `contentFormat: "markdown"`, `description` per the
templates below. Skip `description` only if the detected convention is title-only
**and** the user confirmed that.

**Enrich** (`editJiraIssue`) — `fields: { description: "<markdown>" }`, plus
`summary` only if the retitle was approved. Use `contentFormat: "markdown"`.

**Comment** (`addCommentToJiraIssue`) — `commentBody` with
`contentFormat: "markdown"`. Use this for new evidence on a substantive ticket,
and for images use `jira_browser_ops.py` instead so they embed inline.

Images, whichever path: `jira_browser_ops.py attach <KEY> <img…>`, keep the
`mediaUuid`s, then `jira_browser_ops.py comment <KEY> <spec.json>` for the
human-read TL;DR with the images inline. See
`~/.claude/skills/jira-ticket/references/attachments.md`.

Do not set assignee, priority, labels, or sprint unless asked. New issues may
auto-receive a priority (e.g. `P2 (Major)`) — mention it, leave it.

Act on one row at a time and report failures individually; a rejected field on
row 3 must not silently drop rows 4-6.

### 8. Report

A table of `#` → action taken → key → URL → type → what was attached. Call out
separately: tickets **enriched** (and whether the title changed, `old → new`),
comments added, and anything skipped with the reason. Offer follow-ups; do not
apply them.

## Description templates

Write Jira content in English even when the source thread is in another language —
these are team-facing. Quote original-language lines verbatim as evidence when the
exact wording matters, with a translation next to it.

Include only the sections you have signal for. An honest three-line ticket beats
a padded one with invented repro steps.

**Issue**

```markdown
## Source
- Slack: <thread permalink> (#<channel>, <date>)
- Recording: <slack file link> — not machine-readable; description below is from the thread text

## Reported
<what the reporter saw, in one or two sentences>

## Steps to Reproduce
1. …

## Actual / Expected
- Actual: …
- Expected: …

## Discussion so far
- <Name>: <finding> (<permalink>)

## Key observation
<the one fact that most narrows the cause>

## Open questions
- …
```

**Pending research**

```markdown
## Source
- Slack: <thread permalink> (#<channel>, <date>)

## Question to answer
<the specific thing this ticket exists to determine>

## Why it is open
<unreproducible / cause unknown / not yet bisected / needs a code read>

## What we know
- <Name>: <observation> (<permalink>)

## Suggested next step
<the concrete first action, and who suggested it if known>

## Done when
<what result closes this — an answer is a valid outcome, a fix is not required>
```

## Notes & gotchas

- **Markdown → ADF silently drops illegal nesting.** A blockquote, code block, or
  heading nested inside a list item is not valid ADF, and the converter discards
  **the whole list item**, not just the offending block. The write still returns
  `200`. Keep list items to inline content only — bold, italic, links, inline code
  — and put quotes at top level. Verified the hard way: a `> quote` under a `-`
  bullet made the entire bullet vanish from a created ticket.
- **Always read the body back after a create or enrich.** It is the only way to
  catch the silent drop above. `getJiraIssue … responseContentFormat: markdown`,
  then diff it against what you sent, section by section.
- **Approval gate is not optional.** Even when the user says "just create them",
  show the table first — it takes one turn and catches wrong parents, wrong
  splits, and duplicates.
- **Don't fabricate.** No invented versions, platforms, repro steps, or root
  causes. "Not stated in the thread" is a valid ticket line.
- **Attribute, don't assign.** Naming who reported an issue or who volunteered to
  look at it belongs in the description. Setting the Jira assignee does not,
  unless asked.
- **Timestamps.** Slack renders in the reader's timezone and Jira returns its own
  offset. When a sequence matters (e.g. "was this filed before or after the
  fix?"), normalize and say which zone you used.
- **Re-runs are expected.** The same thread gets processed again after it grows.
  Step 4 is what keeps that from producing twins, so never skip it on a re-run —
  that is exactly when it pays off.
