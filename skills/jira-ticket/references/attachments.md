# Attachments & inline images on Jira tickets

The Atlassian MCP can create/edit issues and post **text** comments. It cannot
upload file attachments, and it cannot embed images inline. Both gaps are filled
by driving Chrome via AppleScript: the helper opens a **dedicated temporary tab**
on a lightweight same-origin page (the REST `myself` endpoint), runs the XHR
there, then closes the tab. Same-origin means it inherits the live Jira session
cookie — no API token needed.

> **Why a fresh tab, not an existing one?** Chrome's memory saver silently
> discards/freezes background tabs (they still report `loading=false` but have no
> live JS context). Running `execute javascript` on a frozen tab hangs until
> `-1712 AppleEvent timed out`. A tab the script just opened is always live, so
> the upload/comment never depends on which tabs the user happens to have open.

The bundled `scripts/jira_browser_ops.py` does all of this. This file explains
what it does under the hood so you can debug or do it by hand if needed.

## The two non-obvious facts

1. **Upload needs the host session, not the MCP.** POST multipart to
   `/rest/api/3/issue/{KEY}/attachments` with header `X-Atlassian-Token: no-check`.
   Run it from a tab already on the Jira host so the cookie rides along.

2. **Inline embedding needs the media UUID, not the attachment id.** The numeric
   attachment id (e.g. `44749`) is rejected by the comment ADF validator with
   `ATTACHMENT_VALIDATION_ERROR`. The ADF `media` node wants the media-services
   **file UUID** (e.g. `939c191f-1cc4-46e3-acce-e1679dd63b3e`). Get it from the
   final redirect of `/rest/api/3/attachment/content/{id}` — `xhr.responseURL`
   lands on `https://api.media.atlassian.com/file/<UUID>/binary?...`.

## ADF media node (what actually renders)

```json
{
  "type": "mediaSingle",
  "attrs": { "layout": "center" },
  "content": [
    { "type": "media",
      "attrs": { "type": "file", "id": "<MEDIA_UUID>", "collection": "" } }
  ]
}
```

Post/replace the comment via `POST` (new) or `PUT` (existing) to
`/rest/api/3/issue/{KEY}/comment[/{commentId}]` with body `{"body": <ADF doc>}`.

## Verifying it rendered (not a broken placeholder)

After posting, GET the comment back with `?expand=renderedBody` and confirm the
HTML contains an `<img ...>` / media element. The helper script returns the POST
status; if you embed by hand, do this read-back check — a 200 with a broken media
node is easy to miss otherwise.

## Manual fallback (no script)

If the script can't run (no Chrome, JS-from-Apple-Events off, non-mac host),
either:
- Ask the user to drag the screenshots into the ticket / comment by hand, or
- If a Jira API token is available, use curl:
  ```bash
  curl -s -u "$JIRA_EMAIL:$JIRA_API_TOKEN" \
    -H "X-Atlassian-Token: no-check" \
    -F "file=@/path/to/shot.png" \
    "https://$JIRA_HOST/rest/api/3/issue/$KEY/attachments"
  ```
  Inline embedding still needs the media UUID via the `content/{id}` redirect.

## Cropping screenshots

Crop to just the relevant region before uploading — a tab bar strip reads far
faster than a full 1206×2622 screen. `PIL` is usually absent; use macOS `sips`:

```bash
cp full.png crop.png
sips --cropOffset <top> <left> --cropToHeightWidth <h> <w> crop.png
```

If `--cropOffset` is unavailable, `sips -c <h> <w> crop.png` center-crops.
