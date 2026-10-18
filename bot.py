
import os
import sqlite3
import logging

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

TOKEN = (
    os.environ.get("TELEGRAM_BOT_TOKEN")
    or os.environ.get("BOT_TOKEN")
)

ADMIN_ID = 8744701224
DB_PATH = os.environ.get("DB_PATH", "mining.db")
GAME_URL = "https://worcoin-mining-bot.mdfahim13711.workers.dev"

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def db_connect():
    return sqlite3.connect(DB_PATH, timeout=15)


def init_db():
    with db_connect() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                coins INTEGER NOT NULL DEFAULT 0
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                url TEXT NOT NULL,
                reward INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS task_claims (
                user_id INTEGER NOT NULL,
                task_id INTEGER NOT NULL,
                PRIMARY KEY (user_id, task_id)
            )
        """)


def get_balance(user_id, username=None):
    with db_connect() as db:
        db.execute(
            "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
            (user_id, username),
        )
        if username:
            db.execute(
                "UPDATE users SET username=? WHERE user_id=?",
                (username, user_id),
            )
        row = db.execute(
            "SELECT coins FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        return row[0]


def home_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "▶️ Play Worcoin",
                web_app=WebAppInfo(url=GAME_URL),
            )
        ],
        [InlineKeyboardButton("⛏️ Tap to Mine", callback_data="tap")],
        [InlineKeyboardButton("📋 Tasks", callback_data="tasks")],
        [InlineKeyboardButton("💰 Balance", callback_data="balance")],
    ])


def task_keyboard(user_id):
    with db_connect() as db:
        tasks = db.execute(
            "SELECT task_id, title, reward FROM tasks "
            "WHERE active=1 ORDER BY task_id"
        ).fetchall()
        claimed = {
            row[0] for row in db.execute(
                "SELECT task_id FROM task_claims WHERE user_id=?",
                (user_id,),
            ).fetchall()
        }

    rows = []
    for tid, title, reward in tasks:
        if tid in claimed:
            rows.append([
                InlineKeyboardButton(
                    f"✅ {title} — Claimed",
                    callback_data="already_claimed",
                )
            ])
        else:
            rows.append([
                InlineKeyboardButton(
                    f"🎁 {title} (+{reward} WOR)",
                    callback_data=f"open:{tid}",
                )
            ])

    if not tasks:
        rows.append([
            InlineKeyboardButton(
                "No tasks available yet", callback_data="noop"
            )
        ])

    rows.append([
        InlineKeyboardButton("🏠 Main Menu", callback_data="home")
    ])
    return InlineKeyboardMarkup(rows)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    coins = get_balance(user.id, user.username)
    await update.message.reply_text(
        "🐍 Welcome to WORCOIN!\n\n"
        f"🪙 Your Balance: {coins} WOR\n\n"
        "Play the game or complete tasks to earn rewards!",
        reply_markup=home_keyboard(),
    )


async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    coins = get_balance(user.id, user.username)
    await update.message.reply_text(f"💰 Your Balance: {coins} WOR")


async def addtask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Admin only.")
        return

    parts = update.message.text.partition(" ")[2].split("|")
    parts = [part.strip() for part in parts]

    if len(parts) != 4:
        await update.message.reply_text(
            "Format:\n/addtask @channel | Task title | "
            "https://t.me/channel | 100"
        )
        return

    chat_id, title, url, reward_text = parts

    if not url.startswith(("https://t.me/", "https://telegram.me/")):
        await update.message.reply_text("Please provide a Telegram link.")
        return

    try:
        reward = int(reward_text)
        if reward <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "Reward must be a positive whole number."
        )
        return

    with db_connect() as db:
        cur = db.execute(
            "INSERT INTO tasks (title, chat_id, url, reward) "
            "VALUES (?, ?, ?, ?)",
            (title, chat_id, url, reward),
        )
        task_id = cur.lastrowid

    await update.message.reply_text(
        f"✅ Task added!\nID: {task_id}\n"
        f"Title: {title}\nReward: {reward} WOR"
    )


async def listtasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Admin only.")
        return

    with db_connect() as db:
        tasks = db.execute(
            "SELECT task_id, title, reward, active "
            "FROM tasks ORDER BY task_id"
        ).fetchall()

    if not tasks:
        await update.message.reply_text("No tasks yet. Use /addtask.")
        return

    lines = ["📋 WORCOIN Tasks"]
    for tid, title, reward, active in tasks:
        status = "ON" if active else "OFF"
        lines.append(f"#{tid} {status} — {title} — {reward} WOR")

    await update.message.reply_text("\n".join(lines))


async def tasktoggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Admin only.")
        return

    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /tasktoggle 1")
        return

    tid = int(context.args[0])
    with db_connect() as db:
        task = db.execute(
            "SELECT active FROM tasks WHERE task_id=?", (tid,)
        ).fetchone()

        if not task:
            await update.message.reply_text("Task not found.")
            return

        new_status = 0 if task[0] else 1
        db.execute(
            "UPDATE tasks SET active=? WHERE task_id=?",
            (new_status, tid),
        )

    await update.message.reply_text(
        f"Task #{tid} is now {'ON' if new_status else 'OFF'}."
    )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Admin only.")
        return

    with db_connect() as db:
        members = db.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]
        total = db.execute(
            "SELECT COALESCE(SUM(coins), 0) FROM users"
        ).fetchone()[0]
        active_tasks = db.execute(
            "SELECT COUNT(*) FROM tasks WHERE active=1"
        ).fetchone()[0]

    await update.message.reply_text(
        f"🛠 WORCOIN Admin\nMembers: {members}\n"
        f"Total WOR: {total}\nActive tasks: {active_tasks}"
    )


async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user = q.from_user
    data = q.data

    if data == "noop":
        await q.answer()
        return

    if data == "already_claimed":
        await q.answer("This reward was already claimed.", show_alert=True)
        return

    if data == "home":
        await q.answer()
        coins = get_balance(user.id, user.username)
        await q.edit_message_text(
            f"🐍 WORCOIN\n🪙 Balance: {coins} WOR",
            reply_markup=home_keyboard(),
        )
        return

    if data == "tap":
        with db_connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO users (user_id, username) "
                "VALUES (?, ?)",
                (user.id, user.username),
            )
            db.execute(
                "UPDATE users SET coins=coins+1 WHERE user_id=?",
                (user.id,),
            )
            coins = db.execute(
                "SELECT coins FROM users WHERE user_id=?", (user.id,)
            ).fetchone()[0]

        await q.answer("+1 WOR!")
        await q.edit_message_text(
            f"🐍 WORCOIN Mining\n\n"
            f"🪙 Balance: {coins} WOR\n✅ +1 WOR",
            reply_markup=home_keyboard(),
        )
        return

    if data == "balance":
        await q.answer()
        coins = get_balance(user.id, user.username)
        await q.edit_message_text(
            f"💰 Balance: {coins} WOR",
            reply_markup=home_keyboard(),
        )
        return

    if data == "tasks":
        await q.answer()
        await q.edit_message_text(
            "📋 Tasks\n\n"
            "Open a task, join its Telegram channel/group, "
            "then verify membership to claim the reward.",
            reply_markup=task_keyboard(user.id),
        )
        return

    if data.startswith("open:"):
        await q.answer()
        tid = int(data.split(":")[1])

        with db_connect() as db:
            task = db.execute(
                "SELECT title, chat_id, url, reward FROM tasks "
                "WHERE task_id=? AND active=1",
                (tid,),
            ).fetchone()
            claimed = db.execute(
                "SELECT 1 FROM task_claims WHERE user_id=? AND task_id=?",
                (user.id, tid),
            ).fetchone()

        if not task:
            await q.edit_message_text(
                "Task unavailable.", reply_markup=task_keyboard(user.id)
            )
            return

        if claimed:
            await q.edit_message_text(
                "Reward already claimed.",
                reply_markup=task_keyboard(user.id),
            )
            return

        title, chat_id, url, reward = task
        await q.edit_message_text(
            f"📋 {title}\n🎁 Reward: {reward} WOR\n\n"
            "1. Join the channel/group.\n"
            "2. Return and press Verify Join.\n"
            "3. Claim your reward.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 Join", url=url)],
                [InlineKeyboardButton(
                    "🔎 Verify Join", callback_data=f"verify:{tid}"
                )],
                [InlineKeyboardButton("⬅️ Tasks", callback_data="tasks")],
            ]),
        )
        return

    if data.startswith(("verify:", "claim:")):
        await q.answer()
        action, tid_text = data.split(":")
        tid = int(tid_text)

        with db_connect() as db:
            task = db.execute(
                "SELECT title, chat_id, url, reward FROM tasks "
                "WHERE task_id=? AND active=1",
                (tid,),
            ).fetchone()
            claimed = db.execute(
                "SELECT 1 FROM task_claims WHERE user_id=? AND task_id=?",
                (user.id, tid),
            ).fetchone()

        if not task:
            await q.edit_message_text(
                "Task unavailable.", reply_markup=task_keyboard(user.id)
            )
            return

        if claimed:
            await q.edit_message_text(
                "Reward already claimed.",
                reply_markup=task_keyboard(user.id),
            )
            return

        title, chat_id, url, reward = task

        try:
            member = await context.bot.get_chat_member(chat_id, user.id)
            is_member = member.status in (
                "member", "administrator", "creator"
            )
            if member.status == "restricted":
                is_member = getattr(member, "is_member", False)
        except TelegramError:
            log.exception("Join verification error")
            await q.edit_message_text(
                "Verification failed. Please make the bot an admin "
                "in the channel/group, then try again.",
                reply_markup=task_keyboard(user.id),
            )
            return

        if not is_member:
            await q.edit_message_text(
                "Please join the channel/group first, then verify.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📢 Join", url=url)],
                    [InlineKeyboardButton(
                        "🔎 Verify Join", callback_data=f"verify:{tid}"
                    )],
                    [InlineKeyboardButton("⬅️ Tasks", callback_data="tasks")],
                ]),
            )
            return

        if action == "verify":
            await q.edit_message_text(
                f"✅ Membership verified!\n\n{title}\n"
                f"🎁 Reward: {reward} WOR",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "🎁 Claim Reward", callback_data=f"claim:{tid}"
                    )],
                    [InlineKeyboardButton("⬅️ Tasks", callback_data="tasks")],
                ]),
            )
            return

        with db_connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT OR IGNORE INTO users (user_id, username) "
                "VALUES (?, ?)",
                (user.id, user.username),
            )
            cur = db.execute(
                "INSERT OR IGNORE INTO task_claims (user_id, task_id) "
                "VALUES (?, ?)",
                (user.id, tid),
            )

            if cur.rowcount == 0:
                db.rollback()
                await q.edit_message_text("Reward already claimed.")
                return

            db.execute(
                "UPDATE users SET coins=coins+? WHERE user_id=?",
                (reward, user.id),
            )
            coins = db.execute(
                "SELECT coins FROM users WHERE user_id=?", (user.id,)
            ).fetchone()[0]
            db.commit()

        await q.edit_message_text(
            f"🎉 Claim successful!\n✅ +{reward} WOR\n"
            f"🪙 New balance: {coins} WOR",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 More Tasks", callback_data="tasks")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="home")],
            ]),
        )


def main():
    if not TOKEN:
        raise RuntimeError(
            "Set TELEGRAM_BOT_TOKEN in hosting environment variables."
        )

    init_db()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", balance_command))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("addtask", addtask))
    app.add_handler(CommandHandler("listtasks", listtasks))
    app.add_handler(CommandHandler("tasktoggle", tasktoggle))
    app.add_handler(CallbackQueryHandler(buttons))

    log.info("WORCOIN bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
