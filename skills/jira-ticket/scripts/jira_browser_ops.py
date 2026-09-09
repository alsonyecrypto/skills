#!/usr/bin/env python3
"""Browser-session Jira ops: upload attachments + post inline-image comments.

Why this exists
---------------
The Atlassian MCP can create/edit issues and post *text* comments, but it
CANNOT upload file attachments, and inline images need a media-services file
UUID that the MCP never exposes. This helper drives the user's already-signed-in
Chrome tab via AppleScript: it runs same-origin XHR against the Jira REST API,
so it inherits the live session cookie — no API token needed.

Two subcommands:
  attach  <ISSUE-KEY> <img...>      Upload images, return per-file
                                    {filename, attachmentId, mediaUuid} as JSON.
  comment <ISSUE-KEY> <spec.json>   Create/replace a comment with a short TL;DR
                                    plus images embedded inline (by mediaUuid).

comment spec.json shape:
  {
    "commentId": "new",            # or an existing comment id to replace
    "tldr": "one or two sentences, plain text",
    "images": [
      {"uuid": "<mediaUuid from attach>", "caption": "iOS 26 — broken"},
      {"uuid": "<mediaUuid>",             "caption": "iOS 18 — control"}
    ]
  }

Requirements (macOS host):
  - Google Chrome open with a tab on the Jira host, signed in.
  - Chrome > View > Developer > "Allow JavaScript from Apple Events" enabled.
  - JIRA_HOST must be set, e.g. JIRA_HOST=your-site.atlassian.net.
"""
import base64
import json
import os
import subprocess
import sys
import tempfile

JIRA_HOST = os.environ.get("JIRA_HOST", "").strip()


def run_in_chrome(js: str) -> str:
    """Execute `js` inside the first Chrome tab on JIRA_HOST and return its result.

    The JS is written to a temp file and read by AppleScript rather than inlined,
    which sidesteps several layers of shell/AppleScript string escaping (base64
    blobs and ADF JSON would otherwise be a nightmare to quote).
    """
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(js)
        path = f.name
    # Run the JS in a DEDICATED temporary tab rather than an arbitrary existing
    # Jira tab. Chrome's memory saver silently discards/freezes background tabs
    # (they still report loading=false but have no live JS context), and
    # `execute javascript` on a frozen tab hangs until `-1712 AppleEvent timed
    # out`. A fresh tab on a lightweight same-origin page (the REST `myself`
    # endpoint) is always live and same-origin, so the upload/comment XHR
    # inherits the session cookie. The tab is closed afterwards (even on error).
    # `with timeout` lifts AppleScript's ~120s Apple Event ceiling for slow
    # synchronous uploads; the Python subprocess timeout sits just above it.
    probe_url = f"https://{JIRA_HOST}/rest/api/3/myself"
    applescript = (
        'tell application "Google Chrome"\n'
        f'  set jsCode to (read POSIX file "{path}" as «class utf8»)\n'
        "  if (count of windows) is 0 then make new window\n"
        "  set w to front window\n"
        f'  set t to (make new tab at end of tabs of w with properties {{URL:"{probe_url}"}})\n'
        "  repeat 75 times\n"
        "    if (loading of t) is false then exit repeat\n"
        "    delay 0.2\n"
        "  end repeat\n"
        "  delay 0.3\n"
        '  set res to "ERR_NO_RESULT"\n'
        "  try\n"
        "    with timeout of 300 seconds\n"
        "      set res to (execute t javascript jsCode)\n"
        "    end timeout\n"
        "  on error errMsg number errNum\n"
        "    close t\n"
        "    error errMsg number errNum\n"
        "  end try\n"
        "  close t\n"
        "  return res\n"
        "end tell"
    )
    try:
        proc = subprocess.run(
            ["osascript", "-e", applescript],
            capture_output=True, text=True, timeout=330,
        )
    finally:
        os.unlink(path)

    res = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0 or not res:
        low = err.lower()
        if "javascript" in low and ("apple events" in low or "not allowed" in low or "turned off" in low):
            sys.exit(
                "ERR: Chrome 'Allow JavaScript from Apple Events' is OFF.\n"
                "Enable it once: Chrome menu > View > Developer > Allow JavaScript from Apple Events."
            )
        sys.exit(f"ERR running osascript: {err or 'no output'}")
    if res == "ERR_NO_RESULT":
        sys.exit("ERR: the page returned no result. Confirm Chrome is signed in "
                 f"to {JIRA_HOST}, then retry.")
    return res


def js_attach(issue: str, images: list) -> str:
    items = []
    for p in images:
        with open(p, "rb") as fh:
            items.append({"name": os.path.basename(p),
                          "b64": base64.b64encode(fh.read()).decode()})
    data_js = json.dumps(items)  # ASCII; base64 is safe through AppleScript
    template = r"""(function(){try{
var data=__DATA__;
function toBlob(b64){var bin=atob(b64),n=bin.length,a=new Uint8Array(n);for(var i=0;i<n;i++)a[i]=bin.charCodeAt(i);return new Blob([a],{type:'image/png'});}
var fd=new FormData();
for(var j=0;j<data.length;j++){fd.append('file',toBlob(data[j].b64),data[j].name);}
var x=new XMLHttpRequest();
x.open('POST','/rest/api/3/issue/__ISSUE__/attachments',false);
x.setRequestHeader('X-Atlassian-Token','no-check');
x.send(fd);
if(x.status>=400)return 'ERR_UPLOAD '+x.status+' '+x.responseText.slice(0,300);
var atts=JSON.parse(x.responseText),out=[];
for(var k=0;k<atts.length;k++){
  var c=new XMLHttpRequest();c.open('GET','/rest/api/3/attachment/content/'+atts[k].id,false);c.send();
  var m=(c.responseURL||'').match(/\/file\/([0-9a-f\-]{36})/);
  out.push({filename:atts[k].filename,attachmentId:atts[k].id,mediaUuid:m?m[1]:''});
}
return JSON.stringify(out);
}catch(e){return 'ERR '+e.message;}})();"""
    return template.replace("__DATA__", data_js).replace("__ISSUE__", issue)


def build_adf(spec: dict) -> dict:
    content = [{
        "type": "paragraph",
        "content": [
            {"type": "text", "text": "👀 Human TL;DR: ", "marks": [{"type": "strong"}]},
            {"type": "text", "text": spec["tldr"]},
        ],
    }]
    for img in spec.get("images", []):
        if img.get("caption"):
            content.append({
                "type": "paragraph",
                "content": [{"type": "text", "text": img["caption"], "marks": [{"type": "strong"}]}],
            })
        content.append({
            "type": "mediaSingle",
            "attrs": {"layout": "center"},
            "content": [{
                "type": "media",
                "attrs": {"type": "file", "id": img["uuid"], "collection": ""},
            }],
        })
    return {"version": 1, "type": "doc", "content": content}


def js_comment(issue: str, spec: dict) -> str:
    cid = str(spec.get("commentId", "new"))
    method = "POST" if cid == "new" else "PUT"
    url = f"/rest/api/3/issue/{issue}/comment" + ("" if cid == "new" else f"/{cid}")
    body = json.dumps({"body": build_adf(spec)})  # ASCII-escaped; valid JS literal
    template = r"""(function(){try{
var x=new XMLHttpRequest();
x.open('__METHOD__','__URL__',false);
x.setRequestHeader('Content-Type','application/json');
x.setRequestHeader('X-Atlassian-Token','no-check');
x.send(JSON.stringify(__BODY__));
return 'STATUS '+x.status+' :: '+x.responseText.slice(0,200);
}catch(e){return 'ERR '+e.message;}})();"""
    return (template.replace("__METHOD__", method)
                    .replace("__URL__", url)
                    .replace("__BODY__", body))


def main():
    if not JIRA_HOST:
        sys.exit("ERR: JIRA_HOST is not set. "
                 "Run with e.g. JIRA_HOST=your-site.atlassian.net")
    if len(sys.argv) < 3:
        sys.exit("usage: jira_browser_ops.py attach <ISSUE> <img...> | "
                 "comment <ISSUE> <spec.json>")
    cmd, issue = sys.argv[1], sys.argv[2]
    if cmd == "attach":
        if len(sys.argv) < 4:
            sys.exit("usage: jira_browser_ops.py attach <ISSUE> <img...>")
        print(run_in_chrome(js_attach(issue, sys.argv[3:])))
    elif cmd == "comment":
        if len(sys.argv) < 4:
            sys.exit("usage: jira_browser_ops.py comment <ISSUE> <spec.json>")
        with open(sys.argv[3]) as fh:
            spec = json.load(fh)
        print(run_in_chrome(js_comment(issue, spec)))
    else:
        sys.exit(f"unknown command: {cmd}")


if __name__ == "__main__":
    main()
