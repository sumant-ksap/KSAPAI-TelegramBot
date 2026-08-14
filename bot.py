import asyncio
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from config import load_config
from db import Memory
from ollama_client import OllamaClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


class _HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format, *args) -> None:  # noqa: A002 - stdlib signature
        pass  # keep health-check/keep-alive pings out of the app log


def start_health_server_if_needed() -> ThreadingHTTPServer | None:
    """Telegram long-polling itself needs no open port. This exists only for
    hosts (like Render's free Web Service tier) that require the process to
    bind $PORT and answer HTTP - both for their own health check and to give
    an external uptime pinger something to hit to prevent idle sleep. A no-op
    unless PORT is set, so local runs and paid Background Workers are unaffected."""
    port = os.getenv("PORT")
    if not port:
        return None
    server = ThreadingHTTPServer(("0.0.0.0", int(port)), _HealthCheckHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info("Health check server listening on port %s", port)
    return server


TELEGRAM_MESSAGE_LIMIT = 4096


def split_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Split text into Telegram-sendable chunks, preferring to break on a
    newline near the limit so paragraphs don't get cut mid-sentence."""
    if len(text) <= limit:
        return [text] if text else []

    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, limit)
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    return chunks


async def reply_in_chunks(message, text: str) -> None:
    try:
        for chunk in split_message(text):
            await message.reply_text(chunk)
    except Exception:
        logger.exception("Failed to send reply to chat %s", message.chat_id)


WELCOME_TEXT = (
    "Hi, I'm KSAP AI Desktop Support. Ask me anything about KSAP's Oracle OTM, GTM, and WMS "
    "services, our products like YMS AI or Logistics Data Studio, or how to get in touch with a "
    "practitioner.\n\n"
    "Commands:\n"
    "/reset - forget this chat's conversation history"
)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or not message.text:
        return

    chat_id = update.effective_chat.id
    memory: Memory = context.bot_data["memory"]
    ollama: OllamaClient = context.bot_data["ollama"]
    history_limit: int = context.bot_data["history_limit"]

    try:
        await memory.add_message(chat_id, "user", message.text)
        history = await memory.history(chat_id, history_limit)

        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        reply = await ollama.reply(history)
        await memory.add_message(chat_id, "assistant", reply)
    except Exception:
        logger.exception("Failed to handle message for chat %s", chat_id)
        await reply_in_chunks(message, "Sorry, I ran into a problem. Please try again.")
        return

    await reply_in_chunks(message, reply)


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is not None:
        await message.reply_text(WELCOME_TEXT)


async def handle_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return

    chat_id = update.effective_chat.id
    memory: Memory = context.bot_data["memory"]
    await memory.reset(chat_id)
    await message.reply_text("Cleared our conversation history. How can I help?")


async def main() -> None:
    health_server = start_health_server_if_needed()
    config = load_config()
    memory = await Memory.connect(config.postgres_dsn)
    ollama = OllamaClient(config.ollama_host, config.ollama_model, config.ollama_api_key)

    application = Application.builder().token(config.telegram_token).build()
    application.bot_data["memory"] = memory
    application.bot_data["ollama"] = ollama
    application.bot_data["history_limit"] = config.history_limit
    application.add_handler(CommandHandler("start", handle_start))
    application.add_handler(CommandHandler("reset", handle_reset))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    try:
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        logger.info("Bot started, polling for updates")
        await asyncio.Event().wait()
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
        await ollama.close()
        await memory.close()
        if health_server is not None:
            health_server.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
