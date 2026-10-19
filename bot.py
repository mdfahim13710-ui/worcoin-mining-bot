import os
import sqlite3
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("BOT_TOKEN")
ADMIN_ID = 8744701224
DB_PATH = os.environ.get("DB_PATH", "mining.db")
GAME_URL = "https://worcoin-mining-bot.mdfahim13711.workers.dev"

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

DEFAULT_BOOSTERS = [
    ("tap", "⚡ Tap Multiplier", 100, 2, 1),
    ("energy", "🔋 Energy Limit", 150, 100, 1),
    ("refill", "🔄 Energy Refill", 50, 100, 1),
    ("cobra", "🐍 Cobra Power", 250, 1, 1),
]


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"WORCOIN bot is running"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        return


def run_health_server():
    port = int(os.environ.get("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    log.info("Health server listening on 0.0.0.0:%s", port)
    server.serve_forever()


def db_connect():
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.execute("PRAGMA foreign_keys=ON")
    return db


def init_db():
    with db_connect() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                coins INTEGER NOT NULL DEFAULT 0,
                tap_power INTEGER NOT NULL DEFAULT 1,
                energy_limit INTEGER NOT NULL DEFAULT 500,
                energy INTEGER NOT NULL DEFAULT 500
            )
        """)
        cols = {r[1] for r in db.execute("PRAGMA table_info(users)")}
        for name, definition in [
            ("tap_power", "INTEGER NOT NULL DEFAULT 1"),
            ("energy_limit", "INTEGER NOT NULL DEFAULT 500"),
            ("energy", "INTEGER NOT NULL DEFAULT 500"),
        ]:
            if name not in cols:
                db.execute(f"ALTER TABLE users ADD COLUMN {name} {definition}")

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
        db.execute("""
            CREATE TABLE IF NOT EXISTS boosters (
                code TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                price INTEGER NOT NULL,
                value INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS booster_levels (
                user_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                level INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, code)
            )
        """)
        for code, title, price, value, active in DEFAULT_BOOSTERS:
            db.execute(
                """INSERT OR IGNORE INTO boosters
                   (code, title, price, value, active) VALUES (?, ?, ?, ?, ?)""",
                (code, title, price, value, active),
            )


def ensure_user(user_id, username=None):
    with db_connect() as db:
        db.execute(
            "INSERT OR IGNORE INTO users(user_id, username) VALUES(?, ?)",
            (user_id, username),
        )
        if username:
            db.execute(
                "UPDATE users SET username=? WHERE user_id=?",
                (username, user_id),
            )
        return db.execute(
            "SELECT coins, tap_power, energy_limit, energy FROM users WHERE user_id=?",
            (user_id,),
        ).fetchone()


def get_balance(user_id, username=None):
    return ensure_user(user_id, username)[0]


def home_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Play Worcoin", web_app=WebAppInfo(url=GAME_URL))],
        [InlineKeyboardButton("⚡ Booster Shop", callback_data="boosters")],
        [InlineKeyboardButton("⛏️ Tap to Mine", callback_data="tap")],
        [
            InlineKeyboardButton("📋 Tasks", callback_data="tasks"),
            InlineKeyboardButton("💰 Balance", callback_data="balance"),
        ],
    ])


def main_menu_button():
    return [InlineKeyboardButton("🏠 Main Menu", callback_data="home")]


def task_keyboard(user_id):
    with db_connect() as db:
        tasks = db.execute(
            "SELECT task_id, title, reward FROM tasks WHERE active=1 ORDER BY task_id"
        ).fetchall()
        claimed = {
            r[0] for r in db.execute(
                "SELECT task_id FROM task_claims WHERE user_id=?",
                (user_id,),
            ).fetchall()
        }

    rows = []
    for tid, title, reward in tasks:
        label = ("✅ " if tid in claimed else "🎁 ") + title
        label += " — Claimed" if tid in claimed else f" (+{reward} WOR)"
        rows.append([InlineKeyboardButton(
            label,
            callback_data="already_claimed" if tid in claimed else f"open:{tid}",
        )])

    if not tasks:
        rows.append([InlineKeyboardButton("No tasks available yet", callback_data="noop")])
    rows.append(main_menu_button())
    return InlineKeyboardMarkup(rows)


def booster_keyboard():
    with db_connect() as db:
        boosters = db.execute(
            "SELECT code, title, price FROM boosters WHERE active=1 ORDER BY code"
        ).fetchall()

    buttons = [
        [InlineKeyboardButton(f"{title} · {price} WOR", callback_data=f"buy:{code}")]
        for code, title, price in boosters
    ]
    if not buttons:
        buttons = [[InlineKeyboardButton("No boosters available", callback_data="noop")]]
    buttons.append(main_menu_button())
    return InlineKeyboardMarkup(buttons)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    coins = get_balance(user.id, user.username)
    await update.message.reply_text(
        f"🐍 Welcome to WORCOIN!\n\n🪙 Your Balance: {coins} WOR\n\n"
        "Play, mine, upgrade boosters, or complete tasks!",
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

    parts = [p.strip() for p in update.message.text.partition(" ")[2].split("|")]
    if len(parts) != 4 or not all(parts):
        await update.message.reply_text(
            "Format:\n/addtask @channel | Task title | https://t.me/channel | 100"
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
        await update.message.reply_text("Reward must be a positive whole number.")
        return

    with db_connect() as db:
        cur = db.execute(
            "INSERT INTO tasks(title, chat_id, url, reward) VALUES(?, ?, ?, ?)",
            (title, chat_id, url, reward),
        )
        task_id = cur.lastrowid

    await update.message.reply_text(
        f"✅ Task added! ID: {task_id}\nTitle: {title}\nReward: {reward} WOR"
    )


async def listtasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Admin only.")
        return

    with db_connect() as db:
        tasks = db.execute(
            "SELECT task_id, title, reward, active FROM tasks ORDER BY task_id"
        ).fetchall()

    if not tasks:
        await update.message.reply_text("No tasks yet. Use /addtask.")
        return

    text = "📋 WORCOIN Tasks\n" + "\n".join(
        f"#{task_id} {'ON' if active else 'OFF'} — {title} — {reward} WOR"
        for task_id, title, reward, active in tasks
    )
    await update.message.reply_text(text)


async def tasktoggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Admin only.")
        return

    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /tasktoggle 1")
        return

    task_id = int(context.args[0])
    with db_connect() as db:
        row = db.execute(
            "SELECT active FROM tasks WHERE task_id=?",
            (task_id,),
        ).fetchone()
        if not row:
            await update.message.reply_text("Task not found.")
            return
        active = 1 - row[0]
        db.execute("UPDATE tasks SET active=? WHERE task_id=?", (active, task_id))

    await update.message.reply_text(
        f"Task #{task_id} is now {'ON' if active else 'OFF'}."
    )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Admin only.")
        return

    with db_connect() as db:
        members = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        total = db.execute("SELECT COALESCE(SUM(coins), 0) FROM users").fetchone()[0]
        active_tasks = db.execute("SELECT COUNT(*) FROM tasks WHERE active=1").fetchone()[0]

    await update.message.reply_text(
        f"🛠 WORCOIN Admin\nMembers: {members}\n"
        f"Total WOR: {total}\nActive tasks: {active_tasks}"
    )


async def boosters_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user.id, update.effective_user.username)
    await update.message.reply_text(
        "⚡ Booster Shop\nChoose a booster to purchase.",
        reply_markup=booster_keyboard(),
    )


async def boosterset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Admin only.")
        return

    parts = [p.strip() for p in update.message.text.partition(" ")[2].split("|")]
    allowed_codes = {"tap", "energy", "refill", "cobra"}
    if len(parts) != 3 or parts[0] not in allowed_codes:
        await update.message.reply_text(
            "Format: /boosterset tap | 100 | 2\n"
            "Codes: tap, energy, refill, cobra\n"
            "price = WOR price; value = benefit for next purchase."
        )
        return

    code = parts[0]
    try:
        price, value = int(parts[1]), int(parts[2])
        if price < 0 or value < 1:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "Price must be 0 or more; value must be a positive whole number."
        )
        return

    with db_connect() as db:
        db.execute(
            "UPDATE boosters SET price=?, value=? WHERE code=?",
            (price, value, code),
        )
    await update.message.reply_text(
        f"✅ {code} updated. Price: {price} WOR; benefit: {value}."
    )


async def boostertoggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Admin only.")
        return

    if len(context.args) != 1 or context.args[0] not in {"tap", "energy", "refill", "cobra"}:
        await update.message.reply_text(
            "Usage: /boostertoggle tap (codes: tap, energy, refill, cobra)"
        )
        return

    code = context.args[0]
    with db_connect() as db:
        row = db.execute(
            "SELECT active FROM boosters WHERE code=?",
            (code,),
        ).fetchone()
        if not row:
            await update.message.reply_text("Booster not found.")
            return
        active = 1 - row[0]
        db.execute("UPDATE boosters SET active=? WHERE code=?", (active, code))

    await update.message.reply_text(f"{code} is now {'ON' if active else 'OFF'}.")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    user_id = user.id
    data = query.data

    ensure_user(user_id, user.username)

    if data == "home":
        await query.edit_message_text("🐍 WORCOIN Main Menu", reply_markup=home_keyboard())
        return

    if data == "balance":
        coins = get_balance(user_id, user.username)
        await query.edit_message_text(
            f"💰 Your Balance: {coins} WOR",
            reply_markup=InlineKeyboardMarkup([main_menu_button()]),
        )
        return

    if data == "boosters":
        await query.edit_message_text(
            "⚡ Booster Shop\nChoose a booster:",
            reply_markup=booster_keyboard(),
        )
        return

    if data == "tasks":
        await query.edit_message_text(
            "📋 Available Tasks",
            reply_markup=task_keyboard(user_id),
        )
        return

    if data in {"noop", "already_claimed"}:
        if data == "already_claimed":
            await query.answer("You already claimed this task.", show_alert=True)
        return

    if data == "tap":
        with db_connect() as db:
            row = db.execute(
                "SELECT coins, tap_power, energy_limit, energy FROM users WHERE user_id=?",
                (user_id,),
            ).fetchone()
            coins, power, energy_limit, energy = row
            if energy <= 0:
                await query.answer("No energy left. Buy an Energy Refill booster.", show_alert=True)
                return

            earned = max(1, power)
            db.execute(
                "UPDATE users SET coins=coins+?, energy=energy-1 WHERE user_id=?",
                (earned, user_id),
            )
            balance = coins + earned
            energy_left = energy - 1

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"⛏️ TAP +{earned} WOR", callback_data="tap")],
            [
                InlineKeyboardButton("📋 Tasks", callback_data="tasks"),
                InlineKeyboardButton("💰 Balance", callback_data="balance"),
            ],
            main_menu_button(),
        ])
        await query.edit_message_text(
            f"⛏️ Mining WORCOIN\n\n🪙 Balance: {balance} WOR\n"
            f"⚡ Energy: {energy_left}/{energy_limit}",
            reply_markup=keyboard,
        )
        return

    if data.startswith("open:"):
        try:
            task_id = int(data.split(":", 1)[1])
        except ValueError:
            await query.answer("Invalid task.", show_alert=True)
            return

        with db_connect() as db:
            task = db.execute(
                "SELECT title, url, reward FROM tasks WHERE task_id=? AND active=1",
                (task_id,),
            ).fetchone()
            claimed = db.execute(
                "SELECT 1 FROM task_claims WHERE user_id=? AND task_id=?",
                (user_id, task_id),
            ).fetchone()

        if not task:
            await query.answer("Task unavailable.", show_alert=True)
            return
        if claimed:
            await query.answer("Already claimed.", show_alert=True)
            return

        title, url, reward = task
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Open Task", url=url)],
            [InlineKeyboardButton("✅ Verify & Claim", callback_data=f"claim:{task_id}")],
            main_menu_button(),
        ])
        await query.edit_message_text(
            f"📋 {title}\nReward: {reward} WOR\n\nComplete the task, then verify.",
            reply_markup=keyboard,
        )
        return

    if data.startswith("claim:"):
        try:
            task_id = int(data.split(":", 1)[1])
        except ValueError:
            await query.answer("Invalid task.", show_alert=True)
            return

        with db_connect() as db:
            task = db.execute(
                "SELECT title, chat_id, reward FROM tasks WHERE task_id=? AND active=1",
                (task_id,),
            ).fetchone()
            claimed = db.execute(
                "SELECT 1 FROM task_claims WHERE user_id=? AND task_id=?",
                (user_id, task_id),
            ).fetchone()

        if not task:
            await query.answer("Task unavailable.", show_alert=True)
            return
        if claimed:
            await query.answer("Already claimed.", show_alert=True)
            return

        title, chat_id, reward = task
        try:
            member = await context.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if member.status not in ("member", "administrator", "creator"):
                await query.answer("Please join the channel first.", show_alert=True)
                return
        except Exception:
            log.exception("Could not verify task membership for task %s", task_id)
            await query.answer(
                "Could not verify membership. Check the channel ID and bot permissions.",
                show_alert=True,
            )
            return

        # Transaction + primary key prevent a user from claiming the same task twice.
        try:
            with db_connect() as db:
                db.execute(
                    "INSERT INTO task_claims(user_id, task_id) VALUES(?, ?)",
                    (user_id, task_id),
                )
                db.execute(
                    "UPDATE users SET coins=coins+? WHERE user_id=?",
                    (reward, user_id),
                )
                balance = db.execute(
                    "SELECT coins FROM users WHERE user_id=?",
                    (user_id,),
                ).fetchone()[0]
        except sqlite3.IntegrityError:
            await query.answer("Already claimed.", show_alert=True)
            return

        await query.edit_message_text(
            f"✅ Task completed: {title}\n"
            f"🎁 Reward: {reward} WOR\n"
            f"💰 New balance: {balance} WOR",
            reply_markup=InlineKeyboardMarkup([main_menu_button()]),
        )
        return

    if data.startswith("buy:"):
        code = data.split(":", 1)[1]
        with db_connect() as db:
            booster = db.execute(
                "SELECT title, price, value, active FROM boosters WHERE code=?",
                (code,),
            ).fetchone()
            if not booster or not booster[3]:
                await query.answer("Booster unavailable.", show_alert=True)
                return

            title, price, value, _active = booster
            row = db.execute(
                "SELECT coins, tap_power, energy_limit, energy FROM users WHERE user_id=?",
                (user_id,),
            ).fetchone()
            coins, power, energy_limit, energy = row
            if coins < price:
                await query.answer("Not enough WOR coins.", show_alert=True)
                return

            db.execute("UPDATE users SET coins=coins-? WHERE user_id=?", (price, user_id))

            if code == "tap":
                db.execute(
                    "UPDATE users SET tap_power=tap_power+? WHERE user_id=?",
                    (value, user_id),
                )
                benefit = f"Tap power increased by {value}."
            elif code == "energy":
                db.execute(
                    """UPDATE users
                       SET energy_limit=energy_limit+?, energy=MIN(energy+?, energy_limit+?)
                       WHERE user_id=?""",
                    (value, value, value, user_id),
                )
                benefit = f"Energy limit increased by {value}."
            elif code == "refill":
                db.execute(
                    "UPDATE users SET energy=energy_limit WHERE user_id=?",
                    (user_id,),
                )
                benefit = "Energy refilled."
            elif code == "cobra":
                db.execute(
                    "UPDATE users SET tap_power=tap_power+? WHERE user_id=?",
                    (value, user_id),
                )
                benefit = f"Cobra power increased by {value}."
            else:
                await query.answer("Unknown booster.", show_alert=True)
                return

            balance = db.execute(
                "SELECT coins FROM users WHERE user_id=?",
                (user_id,),
            ).fetchone()[0]

        await query.edit_message_text(
            f"✅ Purchased: {title}\n{benefit}\n"
            f"🪙 Spent: {price} WOR\n💰 Balance: {balance} WOR",
            reply_markup=InlineKeyboardMarkup([main_menu_button()]),
        )
        return

    await query.answer("Unknown action.", show_alert=True)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("Unhandled bot error", exc_info=context.error)


def main():
    if not TOKEN:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN environment variable.")

    init_db()
    Thread(target=run_health_server, daemon=True).start()

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", balance_command))
    app.add_handler(CommandHandler("addtask", addtask))
    app.add_handler(CommandHandler("listtasks", listtasks))
    app.add_handler(CommandHandler("tasktoggle", tasktoggle))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("boosters", boosters_command))
    app.add_handler(CommandHandler("boosterset", boosterset))
    app.add_handler(CommandHandler("boostertoggle", boostertoggle))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_error_handler(error_handler)

    log.info("WORCOIN bot starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
