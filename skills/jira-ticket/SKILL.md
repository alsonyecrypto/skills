---
name: jira-ticket
description: Create a well-formed Jira ticket (bug/task) with a two-audience house style — a platform-tagged title, an engineer/AI-detailed description, and a short human-read first comment with screenshots embedded inline. Use this whenever the user wants to "create a ticket", "file a bug", "open a Jira issue", "log this in Jira", "raise an issue under PROJ-123 / an epic", or hand a problem off to Jira — including when they paste a Jira issue URL or epic key and ask to add a child issue, and especially when screenshots should go on the ticket.
---

# Create a Jira ticket

Turn a problem, bug, or finding into a Jira ticket that a teammate can grasp in
seconds and an engineer (or AI) can act on without a follow-up question. The
style below splits those two audiences deliberately:

- **Title** carries the platform + version up front, so triage is instant.
- **Description** is the deep, structured record — written for an engineer or AI
  doing the fix. It can be long.
- **First comment** is the human TL;DR — one or two sentences with the
  before/after screenshots embedded inline, so anyone skimming the board gets it
  immediately without opening the description.

Batching a whole discussion into several tickets → use **`context-to-tickets`**,
which fans out over this one.

## Setup

- **Atlassian MCP** connected, for issue create/read/edit and text comments.
- **`JIRA_HOST`** exported for the attachment script, e.g.
  `JIRA_HOST=your-site.atlassian.net`. It refuses to run without it.
- **macOS + Chrome** signed in to Jira, with Chrome > View > Developer >
  "Allow JavaScript from Apple Events" enabled — only needed for attachments and
  inline images.

## Tools

- Issue create/read/edit and **text** comments → **Atlassian MCP**
  (`createJiraIssue`, `getJiraIssue`, `editJiraIssue`, `getJiraProjectIssueTypesMetadata`, …).
- Attachments and **inline-image** comments → **`scripts/jira_browser_ops.py`**
  (the MCP cannot upload files or embed images; see `references/attachments.md`
  for why). It drives Chrome via AppleScript — it opens its own temporary tab, so
  nothing depends on which tabs happen to be open.

## Workflow

### 1. Gather inputs (ask only for what's missing)

- **Parent** — the epic/issue key or URL it should live under (e.g. `PROJ-42`).
- **Platform(s) + version(s)** — which surface is affected (iOS / Android / web /
  backend), and the version where it reproduces. This drives the title tag.
- **The problem** — enough to write repro steps, actual vs expected, and (if
  known) root cause. Pull this from the conversation; don't re-interrogate.
- **Screenshots** — paths to any images. Crop them to the relevant region first
  (see `references/attachments.md`); a tight crop reads far faster than a full
  screen.

### 2. Resolve the parent

Fetch the parent with `getJiraIssue` (fields `issuetype,project`) to learn its
project and hierarchy. If it's an **Epic**, the new issue links to it via the
`parent` field. Then `getJiraProjectIssueTypesMetadata` to confirm the child type
you want (usually **Bug** for a defect, **Task** for work) exists in that project.

> **Hierarchy is by level, not by name.** Only types at `hierarchyLevel: 0` can be
> a child of an Epic (`hierarchyLevel: 1`). Sub-task-ish types sit at `-1` and can
> only hang off a level-0 issue, and some projects put custom types *above* Epic at
> level `2`. Read the metadata; never infer from the type's name.

### 3. Create the issue (MCP `createJiraIssue`)

- `parent`: the epic key. On company-managed projects this usually works directly;
  if the project rejects it, fall back to that project's Epic Link field.
- `issueTypeName`: `Bug` / `Task`.
- `summary`: follow the **Title format** below.
- `contentFormat: "markdown"`, `description`: follow the **Description template**.

Then read the issue back (`getJiraIssue … responseContentFormat: markdown`) and
diff the description against what you sent. Markdown → ADF conversion drops
illegal nesting silently and still returns `200` — see **Notes & gotchas**.

### 4. Attach screenshots

```bash
JIRA_HOST=your-site.atlassian.net \
python3 ~/.claude/skills/jira-ticket/scripts/jira_browser_ops.py \
  attach <ISSUE-KEY> /path/crop_before.png /path/crop_control.png
```

It prints one JSON array of `{filename, attachmentId, mediaUuid}`. **Keep the
`mediaUuid` values** — the inline comment needs them (the numeric attachment id
will not embed; see `references/attachments.md`).

### 5. Post the human-read comment with inline images

Write a spec file, then run the `comment` subcommand:

```json
{
  "commentId": "new",
  "tldr": "On iOS 26 only, the bottom tab bar label is cut off with uneven spacing on cold launch — tapping any tab fixes it. iOS 18 is fine (control below).",
  "images": [
    {"uuid": "<mediaUuid for before>",  "caption": "iOS 26.0 — broken (cold launch)"},
    {"uuid": "<mediaUuid for control>", "caption": "iOS 18.6 — correct (control)"}
  ]
}
```

```bash
JIRA_HOST=your-site.atlassian.net \
python3 ~/.claude/skills/jira-ticket/scripts/jira_browser_ops.py \
  comment <ISSUE-KEY> /tmp/comment_spec.json
```

A `STATUS 200`/`201` means it posted. The script reads images by `mediaUuid`, so
step 4 must run first.

### 6. Confirm and report

Give the user the ticket URL, type, parent, and what was attached. Offer (don't
auto-apply) follow-ups: labels, assignee, priority. Don't change
private↔public visibility or assignees without being asked.

## Title format

Lead with platform + version tags, then a specific, scannable summary.

```
[<platform> <version>] <concise what-and-where>
```

- One platform: `[iOS 26.0] ...`, `[Android 14] ...`, `[RN 0.81] ...`
- Several: stack the tags — `[iOS 26.0] [Android 14] ...`
- Everywhere: `[All] ...`
- Prefer the exact version where it reproduces over a vague range.

**Examples**
- `[iOS 26.0] Bottom tab bar label truncated + inconsistent icon/label spacing on first layout`
- `[Android 14] Account balance flashes 0 before the first websocket tick`
- `[All] Onboarding "Continue" button stays disabled after the biometric prompt`

If the project already has a title convention in use, follow that instead — an
existing board's prefix beats this one.

## Description template

Mark it as the detailed/AI-read record at the top, then a structured body.
Include only the sections you have signal for — don't pad.

```markdown
> **🤖 [Detailed · AI / engineer deep-read]** — Full analysis below. A 1–2 sentence human summary with screenshots is in the first comment.

## Environment
- Platform/version where it reproduces; note where it does NOT.
- Surface / screen / component.

## Steps to Reproduce
1. ...

## Actual Result
What happens, and how reliably (every cold launch / intermittent / …).

## Expected Result
What should happen instead.

## Key Observation
The one fact that most narrows the cause (e.g. "tapping the tab fixes it →
not a hard limit, a first-layout timing glitch").

## Root Cause (analysis)
Code path / known-issue references, if known. Mark guesses as guesses.

## Notes
Control results, workarounds, fixes already tried and their outcome.

## Affected file(s)
Repo-relative paths, e.g. `ios/Sources/Navigation/TabBarController.swift`.
```

## Notes & gotchas

- **Markdown → ADF silently drops illegal nesting.** A blockquote, code block, or
  heading nested inside a list item is not valid ADF, and the converter discards
  **the whole list item**, not just the offending block — while still returning
  `200`. Keep list items to inline content only (bold, italic, links, inline
  code) and put quotes at top level. Always read the body back to confirm.
- **English content.** Titles, descriptions, and comments posted to Jira are
  team-facing — write them in English even if the working conversation is in
  another language.
- **System defaults.** New issues may auto-receive a priority (e.g. `P2 (Major)`).
  Mention it; only change it if asked.
- **Don't fabricate.** If you're unsure of a version, root cause, or affected
  file, say so in the ticket rather than inventing specifics.
- **Attachment/embedding internals** (media UUID, ADF media node, render
  verification, curl/manual fallback) live in `references/attachments.md`. Read
  it if the script errors or you need to embed by hand.
