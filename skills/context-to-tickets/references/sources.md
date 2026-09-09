# Reading context sources

What each kind of link is, how to turn it into an MCP call, and what the tooling
refuses to read.

## Slack link shapes

| Link | Meaning | Call |
| --- | --- | --- |
| `…/archives/<CHANNEL>/p<TS>` | one message (thread parent or reply) | `slack_read_thread(channel_id, message_ts)` |
| `…/archives/<CHANNEL>/p<TS>?thread_ts=<PARENT>&cid=<CHANNEL>` | a reply inside a thread | use **`thread_ts`** as `message_ts` |
| `…/archives/<CHANNEL>` | whole channel | `slack_read_channel(channel_id)` |
| `…/files/<USER>/<FILE_ID>/<name>` | uploaded file | `slack_read_file(file_id=<FILE_ID>)` |

### The `p<TS>` → `message_ts` conversion

Drop the `p`, then put a decimal point **before the last 6 digits**:

```
p1788879046420899  →  1788879046.420899
```

Getting this wrong returns an empty thread rather than an error, so check the
returned parent message actually matches what the user linked.

### When the link points at a reply, not the parent

`slack_read_thread` wants the **parent** `ts`. If the URL carries `thread_ts`,
use that. If it does not and the read comes back with a single message that
obviously reads like a mid-conversation reply, find the parent with
`slack_read_channel` over a window around that timestamp, or
`slack_search_public_and_private` on a distinctive phrase.

### Citing evidence back

Build a permalink per message from its `ts`: strip the dot, prefix `p`.

```
1788916187.285949  →  https://<workspace>.slack.com/archives/<CHANNEL>/p1788916187285949
```

For a reply, append `?thread_ts=<parent ts with dot>&cid=<CHANNEL>` so the link
opens the thread rather than the channel at that point.

## What `slack_read_file` will and will not read

| Type | Result |
| --- | --- |
| Text, code, JSON, CSV | returned as text |
| PNG / JPEG | returned as base64, readable |
| PDF | returned as base64 |
| **`video/*`, `audio/*`** | **refused** — `Binary MCP resource omitted: … is not a supported attachment type` |
| Anything > 10 MB | refused on size |

The video refusal is by **type, not size** — a 2.6 MB mp4 is rejected. There is no
parameter that changes this. Never describe the contents of a recording you did
not actually see; say the recording is not machine-readable and use what the
surrounding thread text says about it.

Screenshots, by contrast, are worth reading. A pasted comparison table or a UI
trial screenshot often carries the only hard numbers in the whole thread.

Canvas files: `slack_read_canvas` returns markdown plus section ids, and is the
better call than `slack_read_file` when the canvas may later need editing.

## Optional: getting content out of a screen recording

Only worth it when the recording is the sole evidence and the thread text is too
thin to write a ticket from. It needs the file on local disk — the MCP cannot
fetch it.

1. **Get the file locally.** Simplest is to ask the user to download it (Slack →
   the file → Download) and give you the path; they are already signed in. A
   browser-automation skill can do it unattended if you have one.

2. **Pull keyframes** (`ffmpeg`; on a Homebrew mac it is at
   `/opt/homebrew/bin/ffmpeg`):

   ```bash
   mkdir -p /tmp/rec_frames
   ffprobe -v error -show_entries format=duration -of csv=p=0 /path/rec.mp4
   # one frame every 0.5 s, scaled down
   ffmpeg -i /path/rec.mp4 -vf "fps=2,scale=540:-1" /tmp/rec_frames/f_%03d.png
   ```

   Read the frames, find the moment the defect appears, and keep only the two or
   three that show it. Crop them with `sips` before attaching (see
   `jira-ticket/references/attachments.md`).

3. **Attach.** `jira_browser_ops.py attach` posts multipart XHR from a live Jira
   tab, so it takes any file type — the mp4 itself can go on the ticket alongside
   the frames.

Say in the ticket that the frames were extracted from the recording, and at what
timestamps.

## Reading a channel window instead of a thread

When the discussion was not threaded, read a window and filter:

```
slack_read_channel(channel_id, oldest="<ts>", latest="<ts>", limit=100)
```

`oldest` / `latest` are Slack `ts` strings. Results come back newest-first. Watch
for the discussion continuing in a thread hanging off one of those messages —
`slack_read_channel` shows the parent, not the replies.

## Resolving names and confirmations

Thread output gives display names and IDs (`<@U01ABCDEF|name>`). For a real name
or email in a ticket, `slack_read_user_profile(user_id)`. Do not map a Slack user
to a Jira account and assign them; attribute in prose instead.

A ✅ reaction on a "is this what you meant?" message is often the only
confirmation a repro path ever gets. `slack_get_reactions(channel_id, message_ts)`
tells you **who** reacted — worth checking before writing "confirmed by the
reporter" into a ticket.
