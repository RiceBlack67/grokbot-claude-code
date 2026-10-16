#!/usr/bin/env python3
"""grokbot_api.py — let Claude Code (or any script) ask your Cursor Grok Bot agents and wait for the reply.

Standard library only. Talks to the same /v0/grokbot session endpoints that Cursor's own
`cursor-grokbot-agents` BDK extension uses. These endpoints are NOT publicly documented:
unofficial, may change or break at any time. Use at your own risk.

  grokbot_api.py list
  grokbot_api.py ask "message" [--agent NAME] [--context-file F] [--wait 540]
  grokbot_api.py check [--agent NAME] [--wait 540]
  grokbot_api.py interrupt [--agent NAME]

Key:       ~/.grokbot/cursor_api_key  (chmod 600) — a PERSONAL Cursor user API key.
           Team/service tokens get "403 Service tokens are not permitted on this endpoint".
Allowlist: GROKBOT_AGENTS="Name A,Name B" (environment, or a line in ~/.grokbot/env).
           Required: the sessions endpoint is get-or-create by name, so a typo would create an empty bot.
State:     ~/.grokbot/cursor_sessions.json — session id + read cursor per agent.
"""
import argparse
import fcntl
import hashlib
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

HOME = os.path.expanduser("~")
BASE = os.environ.get("GROKBOT_HOME", os.path.join(HOME, ".grokbot"))
KEY_FILE = os.path.join(BASE, "cursor_api_key")
ENV_FILE = os.path.join(BASE, "env")
STATE_FILE = os.path.join(BASE, "cursor_sessions.json")
API = os.environ.get("CURSOR_EXTERNAL_API_URL", "https://api.cursor.com").rstrip("/")
DEFAULT_WAIT = 540
POLL_S = 1.0
REQUEST_TIMEOUT_S = 60
# an "idle" report can predate the backend picking up a just-sent message, so require a few in a row
MIN_IDLE_POLLS_WITHOUT_BUSY = 4
CREATED_WINDOW_MS = 60_000
EMPTY_TRANSCRIPT_TAIL = "-1"


class ApiError(Exception):
    pass


def out(obj, code=0):
    print(json.dumps({k: v for k, v in obj.items() if v is not None}, ensure_ascii=False, indent=2))
    sys.exit(code)


def fail(msg, **extra):
    out({"ok": False, "error": msg, **extra}, 1)


def read_env():
    env = {}
    try:
        with open(ENV_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"\'')
    except FileNotFoundError:
        pass
    return env


def allowed_agents():
    raw = os.environ.get("GROKBOT_AGENTS") or read_env().get("GROKBOT_AGENTS", "")
    names = [n.strip().strip('"\'').strip() for n in raw.split(",")]
    names = [n for n in names if n]
    if not names:
        fail('set GROKBOT_AGENTS to your bot name(s) exactly as shown in the Grok Bot app, e.g. GROKBOT_AGENTS="My Grok"')
    return names


def resolve_agent(requested):
    agents = allowed_agents()
    if not requested:
        if len(agents) == 1:
            return agents[0]
        fail("several agents are allowed; pass --agent", allowed=agents)
    for a in agents:
        if a == requested.strip():
            return a
    for a in agents:
        if a.lower() == requested.strip().lower():
            return a
    fail('"%s" is not in the allowlist (GROKBOT_AGENTS)' % requested, allowed=agents)


def api_key():
    try:
        with open(KEY_FILE, encoding="utf-8") as f:
            key = f.read().strip()
    except FileNotFoundError:
        key = ""
    if not key:
        fail("no Cursor API key at %s" % KEY_FILE)
    return key


def request(method, path, body=None):
    headers = {
        "authorization": "Bearer " + api_key(),
        "x-request-id": str(uuid.uuid4()),
    }
    data = None
    if body is not None:
        headers["content-type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")
        msg = text[:300]
        try:
            j = json.loads(text)
            msg = j.get("error") or j.get("message") or msg
            for d in j.get("details") or []:
                detail = (((d or {}).get("debug") or {}).get("details") or {}).get("detail")
                if detail:
                    msg = "%s: %s" % (msg, detail)
        except (ValueError, AttributeError):
            pass
        raise ApiError("%s %s failed (%d): %s" % (method, path.split("?")[0], e.code, msg))
    except (urllib.error.URLError, OSError) as e:
        raise ApiError("%s %s failed: %s" % (method, path.split("?")[0], getattr(e, "reason", e)))


# ---- state (sessionId + read cursor per agent) ----
# Two kinds of lock:
#   * agent_lock(name): held for a whole ask/check on one bot, so replies attribute to the right message.
#     Different bots don't block each other, and interrupt never takes it.
#   * the state-file lock: held only for a quick read or read-modify-write of the JSON.

def _flock(path):
    os.makedirs(BASE, mode=0o700, exist_ok=True)
    f = open(path, "a")
    fcntl.flock(f, fcntl.LOCK_EX)
    return f


class agent_lock:
    def __init__(self, name):
        digest = hashlib.sha1(name.lower().encode()).hexdigest()[:16]
        self.path = os.path.join(BASE, ".lock-agent-" + digest)

    def __enter__(self):
        self.f = _flock(self.path)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


def _load():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def load_state():
    lk = _flock(STATE_FILE + ".lock")
    try:
        return _load()
    finally:
        fcntl.flock(lk, fcntl.LOCK_UN)
        lk.close()


def save_contact(name, contact):
    """Merge one agent's entry into the state file without clobbering other agents."""
    lk = _flock(STATE_FILE + ".lock")
    try:
        data = _load()
        data[name] = contact
        fd, tmp = tempfile.mkstemp(prefix=".sessions-", dir=BASE)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_FILE)
    finally:
        fcntl.flock(lk, fcntl.LOCK_UN)
        lk.close()


def find_contact(data, name):
    return data.get(name) or next((v for k, v in data.items() if k.lower() == name.lower()), None)


def collect(session_id, cursor, deadline, confirm_idle):
    """Read entries after cursor until the turn settles or the deadline passes. Only send-message entries are replies."""
    deliveries, saw_busy, idle_polls = [], False, 0
    while True:
        q = urllib.parse.quote(cursor, safe="")
        page = request("GET", "/v0/grokbot/sessions/%s/entries?afterUpdatedSeq=%s" % (session_id, q))
        cursor = page["latestUpdatedSeq"]
        entries = page.get("entries") or []
        for e in entries:
            if e.get("kind") == "send-message" and e.get("text") is not None:
                deliveries.append(e["text"])
        turn = page.get("turn") or {}
        busy = bool(turn.get("inFlight")) or (turn.get("queued") or 0) > 0
        saw_busy = saw_busy or busy
        if not entries and not busy and turn.get("idle"):
            idle_polls += 1
            if saw_busy or not confirm_idle or idle_polls >= MIN_IDLE_POLLS_WITHOUT_BUSY:
                return "finished", deliveries, cursor
        if time.time() >= deadline:
            return "running", deliveries, cursor
        time.sleep(POLL_S)


def joined(deliveries):
    return "\n\n".join(deliveries) if deliveries else None


def running_note(name, status):
    if status == "running":
        return '"%s" is still replying; run: grokbot_api.py check --agent "%s"' % (name, name)
    return None


def cmd_list(a):
    known = load_state()
    out({"ok": True, "allowed": allowed_agents(),
         "contacted": [{"name": k, "sessionId": v["sessionId"], "lastContactAt": v.get("lastContactAt")}
                       for k, v in known.items()]})


def cmd_ask(a):
    name = resolve_agent(a.agent)
    message = sys.stdin.read() if a.message == "-" else a.message
    if a.context_file:
        with open(a.context_file, encoding="utf-8") as f:
            message += "\n\nContext:\n" + f.read()
    if not message.strip():
        fail("empty message")
    started_ms = int(time.time() * 1000)
    deadline = time.time() + a.wait
    try:
        # get-or-create by name: a name the account doesn't have yet becomes a new empty agent
        s = request("POST", "/v0/grokbot/sessions", {"name": name})
        sid = s["id"]
        with agent_lock(s["name"]):
            contact = find_contact(load_state(), s["name"])
            created = (contact is None and s.get("latestUpdatedSeq") == EMPTY_TRANSCRIPT_TAIL
                       and s.get("createdAtMs", 0) >= started_ms - CREATED_WINDOW_MS)
            # an in-flight turn must settle first, or its deliveries would read as this reply
            st_before, before, cur = collect(sid, (contact or {}).get("cursor") or s["latestUpdatedSeq"],
                                             deadline, confirm_idle=False)
            if st_before == "running":
                fail('"%s" is still working on an earlier message; nothing was sent. Use check, wait, or interrupt.'
                     % s["name"], earlierReplies=before or None)
            if created:
                # stop before sending work into a bot that only exists because of a name mismatch
                save_contact(s["name"], {"sessionId": sid, "cursor": cur, "createdByUs": True,
                                         "firstContactAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
                fail('No Grok Bot agent named "%s" existed on this account, so an empty one was just created. '
                     'Nothing was sent. Check the name in the Grok Bot app.' % s["name"], created=True, sessionId=sid)
            r = request("POST", "/v0/grokbot/sessions/%s/messages" % sid,
                        {"text": message, "messageId": str(uuid.uuid4())})
            if r.get("delivery") not in ("accepted_temporal", "duplicate"):
                fail("harness did not accept the message (delivery: %s)" % r.get("delivery"))
            status, replies, cur = collect(sid, cur, deadline, confirm_idle=True)
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            save_contact(s["name"], {"sessionId": sid, "cursor": cur,
                                     "firstContactAt": (contact or {}).get("firstContactAt", now),
                                     "lastContactAt": now})
    except ApiError as e:
        fail(str(e))
    out({"ok": True, "agent": s["name"], "sessionId": sid, "status": status, "reply": joined(replies),
         "earlierReplies": before or None, "note": running_note(s["name"], status)})


def known_contact(requested):
    name = resolve_agent(requested)
    c = find_contact(load_state(), name)
    if c is None:
        fail('You have not asked "%s" anything yet; use ask first.' % name)
    return name, c


def cmd_check(a):
    name = resolve_agent(a.agent)
    try:
        with agent_lock(name):
            # re-read under the agent lock: a parallel call may have moved the cursor
            name, c = known_contact(name)
            status, replies, cur = collect(c["sessionId"], c["cursor"], time.time() + a.wait, confirm_idle=True)
            c["cursor"] = cur
            save_contact(name, c)
    except ApiError as e:
        fail(str(e))
    out({"ok": True, "agent": name, "sessionId": c["sessionId"], "status": status, "reply": joined(replies),
         "note": running_note(name, status)})


def cmd_interrupt(a):
    # deliberately no agent lock: it must work while an ask on the same bot is still waiting
    name, c = known_contact(a.agent)
    try:
        request("POST", "/v0/grokbot/sessions/%s/interrupt" % c["sessionId"],
                {"reason": "Interrupted by grokbot_api.py."})
    except ApiError as e:
        fail(str(e))
    out({"ok": True, "agent": name, "sessionId": c["sessionId"], "interrupted": True})


def main():
    p = argparse.ArgumentParser(description="Cursor Grok Bot agents via /v0/grokbot")
    sp = p.add_subparsers(dest="cmd", required=True)
    sp.add_parser("list").set_defaults(fn=cmd_list)
    s = sp.add_parser("ask")
    s.add_argument("message", help='message text; "-" reads stdin')
    s.add_argument("--agent")
    s.add_argument("--context-file")
    s.add_argument("--wait", type=int, default=DEFAULT_WAIT)
    s.set_defaults(fn=cmd_ask)
    c = sp.add_parser("check")
    c.add_argument("--agent")
    c.add_argument("--wait", type=int, default=DEFAULT_WAIT)
    c.set_defaults(fn=cmd_check)
    i = sp.add_parser("interrupt")
    i.add_argument("--agent")
    i.set_defaults(fn=cmd_interrupt)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
