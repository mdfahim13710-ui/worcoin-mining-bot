
import os
import logging
import sqlite3

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# =========================
# WORCOIN BOT CONFIGURATION
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEBAPP_URL = os.getenv(
    "WEBAPP_URL",
    "https://mdfahim13710-ui.github.io/worcoin-mining-bot/"
).strip()

DB_PATH = os.getenv("DB_PATH", "worcoin.db")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


# =========================
# DATABASE
# =========================

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()


def save_user(user):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO users (telegram_id, username, first_name)
            VALUES (?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name
        """, (
            user.id,
            user.username or "",
            user.first_name or "",
        ))
        conn.commit()


def get_user_count():
    with sqlite3.connect(DB_PATH) as conn:
        result = conn.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()
        return result[0] if result else 0


# =========================
# /START COMMAND
# =========================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user
    if not user or not update.message:
        return

    save_user(user)

    keyboard = [
        [
            InlineKeyboardButton(
                "🪙 PLAY WORCOIN",
                web_app=WebAppInfo(url=WEBAPP_URL),
            )
        ],
        [
            InlineKeyboardButton(
                "📢 WORCOIN CHANNEL",
                url="https://t.me/worcoin"
            )
        ],
        [
            InlineKeyboardButton(
                "🏆 LEADERBOARD",
                web_app=WebAppInfo(url=WEBAPP_URL)
            )
        ],
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"🪙 Welcome to WORCOIN, {user.first_name}!\n\n"
        "⛏️ Mine WORCOIN\n"
        "🎁 Complete tasks and earn rewards\n"
        "👥 Invite friends\n"
        "🏆 Compete on the leaderboard\n\n"
        "Tap the button below to start playing!",
        reply_markup=reply_markup,
    )


# =========================
# /HELP COMMAND
# =========================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if update.message:
        await update.message.reply_text(
            "🪙 WORCOIN HELP\n\n"
            "/start - Open WORCOIN\n"
            "/help - Show this help message\n"
            "/stats - Show registered user count\n\n"
            "Press PLAY WORCOIN to open the mining app."
        )


# =========================
# /STATS COMMAND
# =========================

async def stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message:
        return

    count = get_user_count()

    await update.message.reply_text(
        f"🪙 WORCOIN BOT STATS\n\n"
        f"👥 Registered users: {count}"
    )


# =========================
# ERROR HANDLER
# =========================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.error(
        "Telegram update error: %s",
        context.error,
    )


# =========================
# MAIN
# =========================

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is missing. Set it in Render Environment Variables."
        )

    if not WEBAPP_URL.startswith("https://"):
        raise RuntimeError(
            "WEBAPP_URL must be a valid HTTPS URL."
        )

    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stats", stats))
    app.add_error_handler(error_handler)

    logger.info("WORCOIN bot is starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
