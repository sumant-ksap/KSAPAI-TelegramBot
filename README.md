# KSAPAI Desktop Support Bot

A Telegram voice-and-chat assistant for KSAP Technologies, built on top of a local/cloud Ollama
LLM, with PostgreSQL persisting each chat's conversation history. It answers questions about KSAP's
Oracle OTM/GTM/WMS services and product portfolio (YMS AI, Logistics Data Studio, SPOT, WMS Billing,
OTMNow Migrate, Platform QA, DataNow), and points people to `info@ksaptech.com` / `+1 888 627-7457`
for anything it can't answer.

## Architecture

Much simpler than a multi-agent workflow - one persona, one system prompt, no tools:

1. **[prompts.py](prompts.py)** holds `SYSTEM_PROMPT`: the assistant's voice-and-chat instructions
   plus the full KSAP company/services/products background. Since replies may be spoken aloud, the
   prompt tells the model to avoid emojis, bullets, and markdown, and to keep answers short and
   conversational.
2. **[ollama_client.py](ollama_client.py)** sends `SYSTEM_PROMPT` plus the chat's recent history to
   Ollama's `/api/chat` and returns the plain-text reply. No tool calling - this bot only needs to
   reason over the background it's already been given.
3. **[db.py](db.py)** persists each chat's message history in Postgres (`messages` table) so replies
   stay coherent across turns; `/reset` clears it.
4. **[bot.py](bot.py)** wires up python-telegram-bot: `/start`, `/reset`, and a plain-text message
   handler that loads history, calls Ollama, saves the reply, and sends it back (chunked if it's
   over Telegram's 4096-character message limit).

## Setup

1. Create a virtualenv and install dependencies:
   ```
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   ```
2. Copy `.env.example` to `.env` and fill in:
   - `TELEGRAM_BOT_TOKEN` - get this from @BotFather. **Do not commit this file.**
   - `OLLAMA_API_HOST` - defaults to `http://localhost:11434`. Deliberately not named `OLLAMA_HOST`
     - Ollama itself often sets that system-wide for its own server bind address (e.g.
     `0.0.0.0:11434`, no scheme), and since `python-dotenv` doesn't override existing environment
     variables by default, a name collision there silently breaks the client request.
   - `OLLAMA_MODEL` - the model tag to use (`ollama pull <model>` first, then confirm the exact tag
     with `ollama list`).
   - `POSTGRES_DSN` - connection string for a reachable Postgres instance. The bot creates its own
     `messages` table on startup.
   - `HISTORY_LIMIT` - how many past messages to feed back to the model as memory (default 20).
3. Run the bot:
   ```
   python bot.py
   ```

## Deploying to Render (so anyone on Telegram can use it)

The one piece that can't run on Render as-is is Ollama itself: `gemma4:31b-cloud` is an Ollama
*Cloud* model normally proxied through a locally signed-in `ollama serve`, and that interactive
sign-in doesn't transfer to a headless host.

The fix: skip the local Ollama proxy entirely and call **Ollama Cloud's own HTTPS API** directly -
`ollama_client.py` already speaks Ollama's native `/api/chat` protocol, so pointing it at
`https://ollama.com` with an `OLLAMA_API_KEY` (bearer token, no `ollama serve` involved) is a drop-in
swap.

**Before deploying**, try one real turn locally with `OLLAMA_API_HOST=https://ollama.com` and
`OLLAMA_API_KEY` set (generate a key at [ollama.com/settings/keys](https://ollama.com/settings/keys))
to confirm the cloud endpoint responds the way you expect.

### Steps

This bot reuses the existing free-tier `travelagent-db` Postgres database rather than provisioning
its own - Render allows only one active free-tier database per account, and that one's already
spoken for by the TravelAgent bot. Its `messages` table name was already taken, so this bot creates
its own `ksapai_messages` table instead (see [db.py](db.py)) - the two bots' conversation histories
stay isolated even though Telegram private-chat IDs are shared across bots for the same user.

1. Push this repo to GitHub, and generate an Ollama Cloud API key.
2. In the Render dashboard, open the `travelagent-db` database and copy its **External Database
   URL** (Internal URL only works for services in the same Render project/region - use External
   unless this service ends up in that same project).
3. **New > Blueprint**, and point it at this repo - it reads [render.yaml](render.yaml) and
   provisions just the web service (no database block, since we're reusing one).
4. Fill in the `sync: false` secrets it prompts for: `TELEGRAM_BOT_TOKEN`, `OLLAMA_API_KEY`, and
   `POSTGRES_DSN` (the External Database URL you copied in step 2).
5. Deploy. Check the logs for `Bot started, polling for updates` with no errors.
6. Message the bot on Telegram to confirm it replies.
7. Set up a free external uptime pinger (e.g. [UptimeRobot](https://uptimerobot.com),
   [cron-job.org](https://cron-job.org)) against the service's `.onrender.com` URL every ~10
   minutes - see the free-tier tradeoffs below for why.

### Free ($0/mo) setup - tradeoffs

[render.yaml](render.yaml) currently targets Render's free tier:

- Render's free tier only exists for **Web Services**, not Background Workers - but a Telegram
  long-polling bot doesn't open a port on its own. `bot.py` starts a trivial stdlib HTTP server
  (`start_health_server_if_needed`) *only* when a `PORT` env var is present (which Render's Web
  Services set automatically), purely so Render considers the process a valid, healthy web service.
  It's a no-op locally and on a real worker.
- Free Web Services **sleep after 15 minutes with no HTTP request** - and Telegram polling doesn't
  count as one. Any message that arrives while asleep waits ~30-60s for a cold start before it's
  even seen. The uptime pinger above is a workaround, not a real fix.
- Free Postgres **deletes all data after 30 days**. This applies to the shared `travelagent-db`
  itself, not something this blueprint provisions - when Render wipes and you recreate that
  database, update `POSTGRES_DSN` here (and in the TravelAgent service) to its new connection
  string. Since this bot has no long-lived state beyond recent chat history, that's low-stakes.

### Paid (~$13/mo) always-on setup

Once you've validated it and want something reliable for real users, switch to a **Background
Worker** (no sleep, no health-check hack needed): in `render.yaml`, change the service's `type` to
`worker` and `plan` to `starter` (and drop `healthCheckPath`). If `travelagent-db` is still on the
free plan at that point, also upgrade it to a paid plan (e.g. `basic-256mb`) so this bot's history
doesn't get wiped every 30 days either - both bots share it, so that upgrade benefits both.

**Cost**: Background Worker Starter $7/mo + Postgres Basic-256mb $6/mo (shared with TravelAgent)
~= **$13/mo total across both bots**, plus your Ollama Cloud plan. Ollama Cloud's Free tier allows
only 1 concurrent model/light usage on a 5-hour session / 7-day rolling cap - fine for trying this
out, but real multi-user traffic across both bots will likely need **Pro ($20/mo, 3 concurrent)** to
avoid throttling.

## Security note

`TELEGRAM_BOT_TOKEN` is a live credential - it lives only in your local `.env` (git-ignored) and in
Render's dashboard as a `sync: false` secret, never committed or printed. If it's ever exposed,
regenerate it via @BotFather (`/revoke` then `/token`) and update it in both places.
