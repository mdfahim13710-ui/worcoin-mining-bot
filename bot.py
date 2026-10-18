
import os
import sqlite3
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = 8744701224
DB_PATH = os.environ.get("DB_PATH", "mining.db")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def init_db():
    with sqlite3.connect(DB_PATH) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                coins INTEGER NOT NULL DEFAULT 0
            )
        """)


def get_user(user_id, username=None):
    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
            (user_id, username),
        )
        if username is not None:
            db.execute(
                "UPDATE users SET username=? WHERE user_id=?",
                (username, user_id),
            )
        row = db.execute(
            "SELECT coins FROM users WHERE user_id=?",
            (user_id,),
        ).fetchone()
        return row[0]


def home_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⛏️ Tap to Mine", callback_data="tap")],
        [InlineKeyboardButton("💰 Balance", callback_data="balance")],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    coins = get_user(user.id, user.username)
    await update.message.reply_text(
        f"🎮 Welcome to Worcoin Mining!\n\n"
        f"🪙 Your balance: {coins} WOR\n\n"
        "Tap the button below to earn coins.",
        reply_markup=home_keyboard(),
    )


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    coins = get_user(user.id, user.username)
    await update.message.reply_text(f"💰 Balance: {coins} WOR")


async def tap_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = query.from_user
    if query.data == "tap":
        with sqlite3.connect(DB_PATH) as db:
            db.execute(
                "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
                (user.id, user.username),
            )
            db.execute(
                "UPDATE users SET coins = coins + 1 WHERE user_id=?",
                (user.id,),
            )
            coins = db.execute(
                "SELECT coins FROM users WHERE user_id=?",
                (user.id,),
            ).fetchone()[0]

        await query.edit_message_text(
            f"🎮 Worcoin Mining\n\n"
            f"🪙 Balance: {coins} WOR\n"
            f"✅ +1 WOR",
            reply_markup=home_keyboard(),
        )

    elif query.data == "balance":
        coins = get_user(user.id, user.username)
        await query.edit_message_text(
            f"💰 Your balance: {coins} WOR",
            reply_markup=home_keyboard(),
        )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Admin only.")
        return

    with sqlite3.connect(DB_PATH) as db:
        members = db.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]
        total = db.execute(
            "SELECT COALESCE(SUM(coins), 0) FROM users"
        ).fetchone()[0]

    await update.message.reply_text(
        f"🛠️ Worcoin Admin Stats\n"
        f"👥 Members: {members}\n"
        f"🪙 Total coins: {total}"
    )


def main():
    if not TOKEN:
        raise RuntimeError("Set BOT_TOKEN in hosting environment variables.")

    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CallbackQueryHandler(tap_button))
    log.info("Worcoin bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
