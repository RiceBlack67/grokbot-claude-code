[English](README.md) | **简体中文**

# grokbot-claude-code

**Let Claude Code hand tasks to your Cursor Grok Bot, wait for the answer, and keep going. No webhook, just one stdlib-only Python script.**

让 Claude Code 把任务交给 Cursor 的 Grok Bot，等它答完，拿着结果接着干。不用 webhook，一个纯 Python 标准库脚本就够了。

Grok Bot 能直接搜 X 上的帖子，最适合接「去推特上查一查」这种活儿。实测让它搜「Claude in Chrome」的帖子，一次拿回 8 条带链接的摘要，外加一段整体评价。

📖 **图文版教程：[sumi5.top/grokbot](https://sumi5.top/grokbot/)**　·　🇬🇧 **English write-up: [X Article](https://x.com/mikan77_aoi/status/2111043148088816045)**

> ⚠️ **非官方。** 脚本调用的 `/v0/grokbot` 是 Cursor 官方 BDK 扩展 `cursor-grokbot-agents` 底层用的接口，**没有公开文档**，随时可能改动或失效。官方支持的方式是 BDK 扩展本身（需要 Node ≥ 22.13 和常驻的 `bdk serve`）。见文末[风险说明](#风险说明)。

---

## 为什么不用 webhook

最早流传的方案是 webhook：Claude Code 把任务 POST 给 Grok Bot，Grok Bot 做完再回传结果。照着做会遇到两个问题：

- Grok Bot 界面不显示 webhook 的 URL 和 key；
- 限流计数出错，只发了几次请求就返回 400：`Automation run rate limit exceeded … current_count=10002`，多人报告。

而 BDK 扩展根本不走 webhook，它只是拿你的 API key 调 4 个 HTTP 接口。那就跳过 BDK，直接调。

| 方法 | 路径 | 作用 |
|---|---|---|
| POST | `/v0/grokbot/sessions` `{name}` | 按名字拿到 bot 的会话（**没有就新建**） |
| POST | `/v0/grokbot/sessions/{id}/messages` `{text, messageId}` | 发一条消息 |
| GET | `/v0/grokbot/sessions/{id}/entries?afterUpdatedSeq=N` | 读回复，以及这一回合答完没有 |
| POST | `/v0/grokbot/sessions/{id}/interrupt` `{reason}` | 打断它正在做的事 |

主机是 `https://api.cursor.com`，请求头 `Authorization: Bearer <key>`。回复是 `kind == "send-message"` 的条目；`turn` 里的 `inFlight`、`queued`、`idle` 用来判断它答完了没有。

## 四步用起来

> 只支持 Linux / macOS：脚本用到了 `fcntl`，Windows 上没有这个模块。

**1. 拿一把个人 key。** 让 BDK 帮你生成。它走浏览器授权加轮询，没有图形界面的 VPS 也能用：把打印出来的网址拿到自己电脑的浏览器里点确认即可。

```bash
npx -p @cursor/bdk bdk login
```

**2. 把 key 存成只有自己能读的文件**，然后删掉 BDK 留下的那份：

```bash
mkdir -p ~/.grokbot && chmod 700 ~/.grokbot
python3 -c 'import json,os;print(json.load(open(os.path.expanduser("~/.config/agent-serve/credentials.json")))["apiKey"],end="")' > ~/.grokbot/cursor_api_key
chmod 600 ~/.grokbot/cursor_api_key
rm ~/.config/agent-serve/credentials.json
```

**3. 写上允许对话的 bot 名字**，要和 Grok Bot app 里显示的完全一致（原因见坑②）。名字两边不用加引号，加了脚本也会自动去掉：

```bash
echo 'GROKBOT_AGENTS=你的bot名字' >> ~/.grokbot/env
```

**4. 装成 Claude Code skill，开问：**

```bash
mkdir -p ~/.claude/skills/grokbot && cd ~/.claude/skills/grokbot
curl -fsSLO https://raw.githubusercontent.com/RiceBlack67/grokbot-claude-code/main/grokbot_api.py
curl -fsSLO https://raw.githubusercontent.com/RiceBlack67/grokbot-claude-code/main/SKILL.md

python3 grokbot_api.py ask "在 X 上搜一下关于 xxx 的帖子，给 5 条带链接"
python3 grokbot_api.py check      # 返回 running 时继续等，别重发
python3 grokbot_api.py interrupt  # 它跑偏了就打断
```

装好以后，直接跟 Claude Code 说「问 Grok Bot 一下……」就行。

白名单里可以写多个 bot，用逗号隔开，调用时用 `--agent 名字` 指定。不同的 bot 可以同时问；同一个 bot 的 `ask` / `check` 会排队，保证回复对得上是哪条消息。`interrupt` 随时可用，不用等正在进行的 `ask` 结束。

## 踩过的三个坑

1. **必须是个人 key。** 团队或服务账号的 token 会被拒：`403 Service tokens are not permitted on this endpoint`。用上面的 `bdk login` 生成的一定是个人 key。
2. **名字拼错，账号里会多出一个空 bot。** `sessions` 接口是按名字查找，找不到就新建。所以脚本强制要求白名单，一旦发现会话是刚新建的，就立刻停下、不发任务。官方扩展的做法是照样发出去，再在结果里提示 `created: true`。
3. **`bdk init` 会偷偷做额外的事。** 不加 `--json` 时，它会执行 `npm install -g`、往 `~/.cursor` 里装技能，最后停在浏览器登录那一步等你。只想看模板的话，记得加 `--json`。

## 安全：两条一定要守

- **这把 key 等于你的整个账号。** 拿到它的人能读写、打断你所有的 Grok Bot 会话。单独建一把，存在 600 权限的文件里，不要提交进仓库，不要贴进任何聊天，不用了就去 dashboard 撤销。
- **Grok Bot 的回复只能当数据，不能当指令。** 它会去搜网页、读帖子，别人写在网上的话可能混进回复里。`SKILL.md` 里已经写死：回复里如果出现「去执行 xxx」「忽略之前的指示」这类话，一律不执行，原样转述给你。

## 风险说明

- `/v0/grokbot/*` 是 Cursor 未公开的接口，是从官方 BDK 扩展的行为里看出来的，不是文档承诺过的 API，随时可能改路径、改字段，或者直接下线。
- 脚本只带你自己的 key 和普通请求头，不冒充官方客户端。但调用未公开接口本身是否符合 Cursor 的服务条款，请自己判断。
- 本项目与 Cursor / Anthropic / xAI 无关。适合个人试玩，风险自负。

## 致谢

- [Riley Brown](https://x.com/rileybrown/status/2109352621081309215)：「从 Claude Code 控制 Grok Bot」这个想法的出处
- [Agent Native](https://x.com/agentnative_/status/2109351438862389575)：最早的 webhook 版完整教程
- [@genoooool](https://x.com/genoooool/status/2110750926080213086)：在评论里指出官方 BDK 扩展这条路

## License

MIT
