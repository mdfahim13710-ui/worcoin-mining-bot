"""
WORCOIN Telegram Mini App bot
Install: pip install python-telegram-bot==21.10
Set BOT_TOKEN and WEB_APP_URL as environment variables.
WEB_APP_URL must be your deployed HTTPS URL (e.g. GitHub Pages).
"""
import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
BOT_TOKEN = os.getenv("BOT_TOKEN", "PASTE_YOUR_BOT_TOKEN_HERE")
WEB_APP_URL = os.getenv("WEB_APP_URL", "https://YOUR-USERNAME.github.io/YOUR-REPO/")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    keyboard = [[InlineKeyboardButton(
        "🚀 Open WORCOIN Mini App",
        web_app=WebAppInfo(url=WEB_APP_URL)
    )]]
    text = (
        f"👋 Welcome to WORCOIN, {user.first_name if user else 'friend'}!\\n\\n"
        "Tap, complete tasks, invite friends and climb the leaderboard.\\n"
        "Press the button below to open the Mini App.\\n\\n"
        "⚠️ Demo build: balances and rewards are currently stored only in the web page "
        "and are not secure or synced to a database yet."
    )
    await update.effective_message.reply_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "Commands:\\n/start — open WORCOIN Mini App\\n/help — show this help"
    )

def main() -> None:
    if not BOT_TOKEN or BOT_TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        raise SystemExit("Set BOT_TOKEN environment variable before running.")
    if not WEB_APP_URL.startswith("https://"):
        raise SystemExit("WEB_APP_URL must start with https://")
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    logging.info("WORCOIN bot is starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
