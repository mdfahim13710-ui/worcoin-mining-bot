
import os
import asyncio
from aiohttp import web
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes
)

TOKEN = os.environ.get("BOT_TOKEN", "PASTE_BOT_TOKEN_HERE")
WEBAPP_URL = os.environ.get(
    "WEBAPP_URL",
    "https://YOUR-DOMAIN.com"
)

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    keyboard = [[
        InlineKeyboardButton(
            "🪙 PLAY COIN TAP",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )
    ]]

    await update.message.reply_text(
        "Welcome to Coin Tap! 🪙\n\n"
        "Tap the button below to open the game.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text(
        "Use /start to open Coin Tap."
    )

async def main():
    if TOKEN == "PASTE_BOT_TOKEN_HERE":
        raise ValueError("Set your Telegram bot token first.")

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))

    print("Telegram bot is starting...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    try:
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
