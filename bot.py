import os
import logging
from threading import Thread
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from telegram import Update, MenuButtonWebApp, WebAppInfo, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = os.getenv("BOT_TOKEN", "").strip()
# Set WEB_APP_URL in your hosting provider to your real GitHub Pages URL.
WEB_APP_URL = os.getenv(
    "WEB_APP_URL",
    "https://mdfahim13710-ui.github.io/worcoin-mining-bot/",
).strip()
PORT = int(os.getenv("PORT", "10000"))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("worcoin-bot")


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"WORCOIN BOT OK"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def run_health_server():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.serve_forever()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[KeyboardButton("🦉 PLAY WORCOIN", web_app=WebAppInfo(url=WEB_APP_URL))]]
    await update.effective_message.reply_text(
        "🪙 WORCOIN Mining Bot\n\nTap, complete tasks, upgrade your miner and climb the leaderboard.",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
    )


async def set_menu(app: Application):
    await app.bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(
            text="🪙 PLAY WORCOIN",
            web_app=WebAppInfo(url=WEB_APP_URL),
        )
    )


async def post_init(app: Application):
    await set_menu(app)


def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is missing.")
    if not WEB_APP_URL.startswith("https://") or "YOUR-USERNAME" in WEB_APP_URL:
        raise RuntimeError("Set WEB_APP_URL to your real HTTPS GitHub Pages URL.")

    Thread(target=run_health_server, daemon=True).start()
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    logger.info("WORCOIN bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
