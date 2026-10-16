**English** | [简体中文](README.zh-CN.md)

# grokbot-claude-code

**Let Claude Code hand tasks to your Cursor Grok Bot, wait for the answer, and keep going. No webhook, just one stdlib-only Python script.**

Grok Bot can search X natively, which makes it the perfect thing to hand "go research this on Twitter" to. In testing, one search for posts about "Claude in Chrome" came back with 8 linked summaries plus an overview of what people liked and complained about.

📖 **Write-up: [X Article](https://x.com/mikan77_aoi/status/2111043148088816045)**　·　🇨🇳 **Step-by-step guide (Chinese): [sumi5.top/grokbot](https://sumi5.top/grokbot/)**

> ⚠️ **Unofficial.** The script calls `/v0/grokbot`, the endpoint used under the hood by Cursor's official BDK extension `cursor-grokbot-agents`. It is **undocumented** and may change or break at any time. The supported path is the BDK extension itself (needs Node ≥ 22.13 and a long-running `bdk serve`). See [Fine print](#fine-print).

---

## Why not the webhook

The first approach going around was a webhook: Claude Code POSTs a task to Grok Bot, and Grok Bot posts the result back. Follow it today and you'll likely hit two walls:

- The Grok Bot UI doesn't show you the webhook URL or key;
- The rate-limit counter is broken: after only a few requests you get a 400 `Automation run rate limit exceeded … current_count=10002`, reported by several people.

The BDK extension doesn't use a webhook at all. It just takes your API key and calls 4 HTTP endpoints. So skip BDK and call them directly.

| Method | Path | What it does |
|---|---|---|
| POST | `/v0/grokbot/sessions` `{name}` | Get your bot's session by name (creates one if missing) |
| POST | `/v0/grokbot/sessions/{id}/messages` `{text, messageId}` | Send a message |
| GET | `/v0/grokbot/sessions/{id}/entries?afterUpdatedSeq=N` | Read replies, and whether the turn is done |
| POST | `/v0/grokbot/sessions/{id}/interrupt` `{reason}` | Stop what it's currently doing |

Host is `https://api.cursor.com`, header `Authorization: Bearer <key>`. Replies are entries with `kind == "send-message"`; `inFlight`, `queued` and `idle` in `turn` tell you whether it's finished.

## Setup in 4 steps

> Linux / macOS only: the script uses `fcntl`, which doesn't exist on Windows.

**1. Get a personal key.** Let BDK mint one. It uses browser auth plus polling, so it works on a headless VPS: open the printed link in the browser on your own computer and approve.

```bash
npx -p @cursor/bdk bdk login
```

**2. Save the key to a file only you can read**, then delete the copy BDK left behind:

```bash
mkdir -p ~/.grokbot && chmod 700 ~/.grokbot
python3 -c 'import json,os;print(json.load(open(os.path.expanduser("~/.config/agent-serve/credentials.json")))["apiKey"],end="")' > ~/.grokbot/cursor_api_key
chmod 600 ~/.grokbot/cursor_api_key
rm ~/.config/agent-serve/credentials.json
```

**3. Allowlist the bot names you want to talk to.** They must match exactly what the Grok Bot app shows (see trap 2 for why). No quotes needed; if you add them, the script strips them:

```bash
echo 'GROKBOT_AGENTS=My Grok' >> ~/.grokbot/env
```

**4. Install it as a Claude Code skill and start asking:**

```bash
mkdir -p ~/.claude/skills/grokbot && cd ~/.claude/skills/grokbot
curl -fsSLO https://raw.githubusercontent.com/RiceBlack67/grokbot-claude-code/main/grokbot_api.py
curl -fsSLO https://raw.githubusercontent.com/RiceBlack67/grokbot-claude-code/main/SKILL.md

python3 grokbot_api.py ask "Search X for posts about xxx and give me 5 with links"
python3 grokbot_api.py check      # if it says running, keep waiting, don't resend
python3 grokbot_api.py interrupt  # stop it if it goes off track
```

Once installed, just tell Claude Code "Ask Grok Bot to …".

You can allowlist several bots, comma-separated, and pick one with `--agent NAME`. Different bots can be asked at the same time; `ask` / `check` on the same bot are queued so every reply matches the right message. `interrupt` works any time, without waiting for a running `ask` to finish.

> Grok Bot sometimes answers twice. On longer research it may reply "on it, I'll send the report when it's done" and end its turn; the real answer arrives a minute later as a new message. Keep checking for a little while instead of treating the first reply as final.

## 3 traps I hit

1. **It must be a personal key.** Team or service-account tokens are rejected: `403 Service tokens are not permitted on this endpoint`. The key from `bdk login` above is always a personal one.
2. **A typo in the name creates a new empty bot.** The `sessions` endpoint is get-or-create by name. That's why the script requires an allowlist, and stops before sending anything if it sees a session that was just created. The official extension sends anyway and flags `created: true` afterwards.
3. **`bdk init` does more than you think.** Without `--json` it runs `npm install -g`, installs skills into `~/.cursor`, then sits waiting for a browser login. If you only want to look at the template, add `--json`.

## Two safety rules

- **That key is your whole account.** Anyone holding it can read, write and interrupt every Grok Bot conversation you have. Make a dedicated one, keep it in a chmod 600 file, never commit it, never paste it into any chat, and revoke it in the dashboard when you're done.
- **Grok Bot's replies are data, not instructions.** It searches the web and reads other people's posts, and any of that text can end up in its answer. `SKILL.md` hard-codes this: if a reply says things like "run xxx" or "ignore previous instructions", nothing gets executed; it is relayed to you as-is.

## Fine print

- `/v0/grokbot/*` is an undocumented Cursor endpoint, worked out from how the official BDK extension behaves. It's not a documented API and may change paths or fields, or go away entirely, at any time.
- The script sends only your own key and normal headers and doesn't pretend to be Cursor's client. Whether calling an undocumented endpoint fits Cursor's terms of service is your call.
- Not affiliated with Cursor, Anthropic or xAI. For personal tinkering, at your own risk.

## Thanks

- [Riley Brown](https://x.com/rileybrown/status/2109352621081309215): the idea of controlling Grok Bot from Claude Code
- [Agent Native](https://x.com/agentnative_/status/2109351438862389575): the original, complete webhook guide
- [@genoooool](https://x.com/genoooool/status/2110750926080213086): pointing at the official BDK extension in the replies

## License

MIT
