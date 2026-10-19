import os
import sqlite3
import logging
import json
import hmac
import hashlib
import time
from urllib.parse import parse_qsl
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
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


# =========================================================
# CONFIG
# =========================================================

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("BOT_TOKEN")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

ADMIN_ID = 8744701224
DB_PATH = os.environ.get("DB_PATH", "mining.db")

GAME_URL = "https://worcoin-mining-bot.mdfahim13711.workers.dev"

API_MAX_INITDATA_AGE = 86400

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


# =========================================================
# SUPABASE HELPERS
# =========================================================

def supabase_ready():
    return bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)


def supabase_request(method, path, body=None, query=None):
    if not supabase_ready():
        raise RuntimeError("Supabase environment variables are missing.")

    url = SUPABASE_URL + path

    if query:
        url += "?" + query

    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": "Bearer " + SUPABASE_SERVICE_ROLE_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    if method.upper() in {"POST", "PATCH", "PUT"}:
        headers["Prefer"] = "return=representation"

    data = None

    if body is not None:
        data = json.dumps(body).encode("utf-8")

    request = Request(
        url,
        data=data,
        headers=headers,
        method=method.upper(),
    )

    try:
        with urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8")

            if not raw:
                return []

            return json.loads(raw)

    except HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Supabase HTTP {e.code}: {error_body}"
        )

    except URLError as e:
        raise RuntimeError(
            f"Supabase connection error: {e}"
        )


def supabase_get_profile(user_id):
    rows = supabase_request(
        "GET",
        "/rest/v1/worcoin_profiles",
        query=(
            "select=telegram_id,username,firstname,balance,"
            "mininglevel,boost_level,refer_count"
            f"&telegram_id=eq.{int(user_id)}"
            "&limit=1"
        ),
    )

    return rows[0] if rows else None


def supabase_create_profile(user_id, username="", firstname=""):
    profile = {
        "telegram_id": int(user_id),
        "username": username or "",
        "firstname": firstname or "",
        "balance": 0,
        "mininglevel": 1,
        "boost_level": 1,
        "refer_count": 0,
    }

    rows = supabase_request(
        "POST",
        "/rest/v1/worcoin_profiles",
        body=profile,
    )

    return rows[0] if rows else profile


def supabase_get_or_create_profile(user_id, username="", firstname=""):
    profile = supabase_get_profile(user_id)

    if profile:
        return profile

    return supabase_create_profile(
        user_id,
        username,
        firstname,
    )


def supabase_update_profile(user_id, values):
    rows = supabase_request(
        "PATCH",
        "/rest/v1/worcoin_profiles",
        body=values,
        query=f"telegram_id=eq.{int(user_id)}",
    )

    return rows[0] if rows else None


# =========================================================
# TELEGRAM WEB APP INIT DATA VERIFICATION
# =========================================================

def validate_telegram_init_data(init_data):
    if not init_data:
        return None

    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))

        received_hash = pairs.pop("hash", None)

        if not received_hash:
            return None

        auth_date = pairs.get("auth_date")

        if not auth_date:
            return None

        try:
            auth_time = int(auth_date)
        except ValueError:
            return None

        if abs(int(time.time()) - auth_time) > API_MAX_INITDATA_AGE:
            return None

        data_check_string = "\n".join(
            f"{key}={pairs[key]}"
            for key in sorted(pairs)
        )

        secret_key = hmac.new(
            b"WebAppData",
            TOKEN.encode("utf-8"),
            hashlib.sha256,
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(
            calculated_hash,
            received_hash,
        ):
            return None

        user_json = pairs.get("user")

        if not user_json:
            return None

        user = json.loads(user_json)

        if not user.get("id"):
            return None

        return user

    except Exception:
        log.exception("Telegram initData validation failed")
        return None


# =========================================================
# HTTP API
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def send_json(self, status, data):
        body = json.dumps(
            data,
            ensure_ascii=False
        ).encode("utf-8")

        self.send_response(status)

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )

        self.send_header(
            "Content-Length",
            str(len(body))
        )

        self.send_header(
            "Access-Control-Allow-Origin",
            "*"
        )

        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, X-Telegram-Init-Data"
        )

        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, OPTIONS"
        )

        self.end_headers()

        self.wfile.write(body)

    def read_json(self):
        try:
            length = int(
                self.headers.get("Content-Length", "0")
            )

            if length <= 0:
                return {}

            raw = self.rfile.read(length)

            return json.loads(
                raw.decode("utf-8")
            )

        except Exception:
            return {}

    def get_user(self):
        init_data = self.headers.get(
            "X-Telegram-Init-Data",
            ""
        )

        return validate_telegram_init_data(
            init_data
        )

    def do_OPTIONS(self):
        self.send_response(204)

        self.send_header(
            "Access-Control-Allow-Origin",
            "*"
        )

        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, X-Telegram-Init-Data"
        )

        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, OPTIONS"
        )

        self.end_headers()

    def do_GET(self):

        # -----------------------------------------
        # Health
        # -----------------------------------------

        if self.path == "/":
            self.send_json(
                200,
                {
                    "status": "ok",
                    "service": "WORCOIN bot"
                }
            )
            return

        # -----------------------------------------
        # PROFILE API
        # -----------------------------------------

        if self.path.startswith("/api/profile"):

            user = self.get_user()

            if not user:
                self.send_json(
                    401,
                    {
                        "success": False,
                        "error": "Invalid Telegram session."
                    }
                )
                return

            try:
                profile = supabase_get_or_create_profile(
                    user_id=user["id"],
                    username=user.get("username", ""),
                    firstname=user.get("first_name", ""),
                )

                self.send_json(
                    200,
                    {
                        "success": True,
                        "profile": profile
                    }
                )

            except Exception as e:
                log.exception("Profile API error")

                self.send_json(
                    500,
                    {
                        "success": False,
                        "error": str(e)
                    }
                )

            return

        # -----------------------------------------
        # TOP 100 API
        # -----------------------------------------

        if self.path.startswith("/api/rank"):

            try:
                rows = supabase_request(
                    "GET",
                    "/rest/v1/worcoin_profiles",
                    query=(
                        "select=username,firstname,balance,"
                        "mininglevel,boost_level,refer_count"
                        "&order=balance.desc"
                        "&limit=100"
                    ),
                )

                ranking = []

                for index, row in enumerate(
                    rows,
                    start=1
                ):
                    ranking.append(
                        {
                            "rank": index,
                            "username": row.get(
                                "username",
                                ""
                            ),
                            "firstname": row.get(
                                "firstname",
                                ""
                            ),
                            "balance": int(
                                row.get(
                                    "balance",
                                    0
                                ) or 0
                            ),
                            "mininglevel": int(
                                row.get(
                                    "mininglevel",
                                    1
                                ) or 1
                            ),
                            "boost_level": int(
                                row.get(
                                    "boost_level",
                                    1
                                ) or 1
                            ),
                            "refer_count": int(
                                row.get(
                                    "refer_count",
                                    0
                                ) or 0
                            ),
                        }
                    )

                self.send_json(
                    200,
                    {
                        "success": True,
                        "count": len(ranking),
                        "ranking": ranking
                    }
                )

            except Exception as e:
                log.exception("Rank API error")

                self.send_json(
                    500,
                    {
                        "success": False,
                        "error": str(e)
                    }
                )

            return

        self.send_json(
            404,
            {
                "success": False,
                "error": "Not found"
            }
        )

    def do_POST(self):

        # -----------------------------------------
        # TAP API
        # -----------------------------------------

        if self.path == "/api/tap":

            user = self.get_user()

            if not user:
                self.send_json(
                    401,
                    {
                        "success": False,
                        "error": "Invalid Telegram session."
                    }
                )
                return

            try:
                user_id = int(user["id"])

                profile = supabase_get_or_create_profile(
                    user_id=user_id,
                    username=user.get("username", ""),
                    firstname=user.get("first_name", ""),
                )

                balance = int(
                    profile.get("balance", 0) or 0
                )

                mining_level = int(
                    profile.get("mininglevel", 1) or 1
                )

                boost_level = int(
                    profile.get("boost_level", 1) or 1
                )

                # Basic server-side anti-cheat:
                # one API request gives one mining reward.
                earned = max(
                    1,
                    mining_level * boost_level
                )

                new_balance = balance + earned

                updated = supabase_update_profile(
                    user_id,
                    {
                        "balance": new_balance
                    }
                )

                if not updated:
                    updated = {
                        **profile,
                        "balance": new_balance
                    }

                self.send_json(
                    200,
                    {
                        "success": True,
                        "earned": earned,
                        "balance": new_balance,
                        "mininglevel": mining_level,
                        "boost_level": boost_level
                    }
                )

            except Exception as e:
                log.exception("Tap API error")

                self.send_json(
                    500,
                    {
                        "success": False,
                        "error": str(e)
                    }
                )

            return

        self.send_json(
            404,
            {
                "success": False,
                "error": "Not found"
            }
        )

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        return


def run_health_server():
    port = int(
        os.environ.get(
            "PORT",
            "10000"
        )
    )

    server = ThreadingHTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    log.info(
        "API server listening on 0.0.0.0:%s",
        port
    )

    server.serve_forever()


# =========================================================
# SQLITE
# =========================================================

def db_connect():
    db = sqlite3.connect(
        DB_PATH,
        timeout=15
    )

    db.execute(
        "PRAGMA foreign_keys=ON"
    )

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

        cols = {
            r[1]
            for r in db.execute(
                "PRAGMA table_info(users)"
            )
        }

        for name, definition in [
            (
                "tap_power",
                "INTEGER NOT NULL DEFAULT 1"
            ),
            (
                "energy_limit",
                "INTEGER NOT NULL DEFAULT 500"
            ),
            (
                "energy",
                "INTEGER NOT NULL DEFAULT 500"
            ),
        ]:

            if name not in cols:

                db.execute(
                    f"ALTER TABLE users ADD COLUMN "
                    f"{name} {definition}"
                )

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
                """
                INSERT OR IGNORE INTO boosters
                (code, title, price, value, active)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    code,
                    title,
                    price,
                    value,
                    active
                )
            )


def ensure_user(user_id, username=None):

    with db_connect() as db:

        db.execute(
            """
            INSERT OR IGNORE INTO users
            (user_id, username)
            VALUES (?, ?)
            """,
            (
                user_id,
                username
            )
        )

        if username:

            db.execute(
                """
                UPDATE users
                SET username=?
                WHERE user_id=?
                """,
                (
                    username,
                    user_id
                )
            )

        return db.execute(
            """
            SELECT coins,
                   tap_power,
                   energy_limit,
                   energy
            FROM users
            WHERE user_id=?
            """,
            (user_id,)
        ).fetchone()


def get_balance(user_id, username=None):
    return ensure_user(
        user_id,
        username
    )[0]


# =========================================================
# TELEGRAM UI
# =========================================================

def home_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "▶️ Play Worcoin",
                web_app=WebAppInfo(
                    url=GAME_URL
                )
            )
        ],
        [
            InlineKeyboardButton(
                "⚡ Booster Shop",
                callback_data="boosters"
            )
        ],
        [
            InlineKeyboardButton(
                "⛏️ Tap to Mine",
                callback_data="tap"
            )
        ],
        [
            InlineKeyboardButton(
                "📋 Tasks",
                callback_data="tasks"
            ),
            InlineKeyboardButton(
                "💰 Balance",
                callback_data="balance"
            )
        ],
    ])


def main_menu_button():

    return [
        InlineKeyboardButton(
            "🏠 Main Menu",
            callback_data="home"
        )
    ]


def task_keyboard(user_id):

    with db_connect() as db:

        tasks = db.execute(
            """
            SELECT task_id, title, reward
            FROM tasks
            WHERE active=1
            ORDER BY task_id
            """
        ).fetchall()

        claimed = {
            r[0]
            for r in db.execute(
                """
                SELECT task_id
                FROM task_claims
                WHERE user_id=?
                """,
                (user_id,)
            ).fetchall()
        }

    rows = []

    for tid, title, reward in tasks:

        label = (
            ("✅ " if tid in claimed else "🎁 ")
            + title
        )

        label += (
            " — Claimed"
            if tid in claimed
            else f" (+{reward} WOR)"
        )

        rows.append([
            InlineKeyboardButton(
                label,
                callback_data=(
                    "already_claimed"
                    if tid in claimed
                    else f"open:{tid}"
                )
            )
        ])

    if not tasks:

        rows.append([
            InlineKeyboardButton(
                "No tasks available yet",
                callback_data="noop"
            )
        ])

    rows.append(
        main_menu_button()
    )

    return InlineKeyboardMarkup(rows)


def booster_keyboard():

    with db_connect() as db:

        boosters = db.execute(
            """
            SELECT code, title, price
            FROM boosters
            WHERE active=1
            ORDER BY code
            """
        ).fetchall()

    buttons = [
        [
            InlineKeyboardButton(
                f"{title} · {price} WOR",
                callback_data=f"buy:{code}"
            )
        ]
        for code, title, price in boosters
    ]

    if not buttons:

        buttons = [[
            InlineKeyboardButton(
                "No boosters available",
                callback_data="noop"
            )
        ]]

    buttons.append(
        main_menu_button()
    )

    return InlineKeyboardMarkup(buttons)


# =========================================================
# COMMANDS
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    coins = get_balance(
        user.id,
        user.username
    )

    await update.message.reply_text(
        f"🐍 Welcome to WORCOIN!\n\n"
        f"🪙 Your Balance: {coins} WOR\n\n"
        "Play, mine, upgrade boosters, or complete tasks!",
        reply_markup=home_keyboard()
    )


async def balance_command(update, context):

    user = update.effective_user

    coins = get_balance(
        user.id,
        user.username
    )

    await update.message.reply_text(
        f"💰 Your Balance: {coins} WOR"
    )


async def addtask(update, context):

    if update.effective_user.id != ADMIN_ID:

        await update.message.reply_text(
            "⛔ Admin only."
        )

        return

    parts = [
        p.strip()
        for p in update.message.text
        .partition(" ")[2]
        .split("|")
    ]

    if len(parts) != 4 or not all(parts):

        await update.message.reply_text(
            "Format:\n"
            "/addtask @channel | Task title | "
            "https://t.me/channel | 100"
        )

        return

    chat_id, title, url, reward_text = parts

    if not url.startswith(
        (
            "https://t.me/",
            "https://telegram.me/"
        )
    ):

        await update.message.reply_text(
            "Please provide a Telegram link."
        )

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
            """
            INSERT INTO tasks
            (title, chat_id, url, reward)
            VALUES (?, ?, ?, ?)
            """,
            (
                title,
                chat_id,
                url,
                reward
            )
        )

        task_id = cur.lastrowid

    await update.message.reply_text(
        f"✅ Task added!\n"
        f"ID: {task_id}\n"
        f"Title: {title}\n"
        f"Reward: {reward} WOR"
    )


async def listtasks(update, context):

    if update.effective_user.id != ADMIN_ID:

        await update.message.reply_text(
            "⛔ Admin only."
        )

        return

    with db_connect() as db:

        tasks = db.execute(
            """
            SELECT task_id,
                   title,
                   reward,
                   active
            FROM tasks
            ORDER BY task_id
            """
        ).fetchall()

    if not tasks:

        await update.message.reply_text(
            "No tasks yet. Use /addtask."
        )

        return

    text = "📋 WORCOIN Tasks\n"

    text += "\n".join(
        f"#{task_id} "
        f"{'ON' if active else 'OFF'} — "
        f"{title} — {reward} WOR"
        for task_id, title, reward, active
        in tasks
    )

    await update.message.reply_text(
        text
    )


async def tasktoggle(update, context):

    if update.effective_user.id != ADMIN_ID:

        await update.message.reply_text(
            "⛔ Admin only."
        )

        return

    if (
        len(context.args) != 1
        or not context.args[0].isdigit()
    ):

        await update.message.reply_text(
            "Usage: /tasktoggle 1"
        )

        return

    task_id = int(
        context.args[0]
    )

    with db_connect() as db:

        row = db.execute(
            """
            SELECT active
            FROM tasks
            WHERE task_id=?
            """,
            (task_id,)
        ).fetchone()

        if not row:

            await update.message.reply_text(
                "Task not found."
            )

            return

        active = 1 - row[0]

        db.execute(
            """
            UPDATE tasks
            SET active=?
            WHERE task_id=?
            """,
            (
                active,
                task_id
            )
        )

    await update.message.reply_text(
        f"Task #{task_id} is now "
        f"{'ON' if active else 'OFF'}."
    )


async def stats(update, context):

    if update.effective_user.id != ADMIN_ID:

        await update.message.reply_text(
            "⛔ Admin only."
        )

        return

    with db_connect() as db:

        members = db.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]

        total = db.execute(
            """
            SELECT COALESCE(SUM(coins), 0)
            FROM users
            """
        ).fetchone()[0]

        active_tasks = db.execute(
            """
            SELECT COUNT(*)
            FROM tasks
            WHERE active=1
            """
        ).fetchone()[0]

    await update.message.reply_text(
        f"🛠 WORCOIN Admin\n"
        f"Members: {members}\n"
        f"Total WOR: {total}\n"
        f"Active tasks: {active_tasks}"
    )


async def boosters_command(update, context):

    ensure_user(
        update.effective_user.id,
        update.effective_user.username
    )

    await update.message.reply_text(
        "⚡ Booster Shop\n"
        "Choose a booster to purchase.",
        reply_markup=booster_keyboard()
    )


async def boosterset(update, context):

    if update.effective_user.id != ADMIN_ID:

        await update.message.reply_text(
            "⛔ Admin only."
        )

        return

    parts = [
        p.strip()
        for p in update.message.text
        .partition(" ")[2]
        .split("|")
    ]

    allowed_codes = {
        "tap",
        "energy",
        "refill",
        "cobra"
    }

    if (
        len(parts) != 3
        or parts[0] not in allowed_codes
    ):

        await update.message.reply_text(
            "Format: /boosterset tap | 100 | 2\n"
            "Codes: tap, energy, refill, cobra"
        )

        return

    code = parts[0]

    try:

        price = int(parts[1])
        value = int(parts[2])

        if price < 0 or value < 1:
            raise ValueError

    except ValueError:

        await update.message.reply_text(
            "Price must be 0 or more; "
            "value must be positive."
        )

        return

    with db_connect() as db:

        db.execute(
            """
            UPDATE boosters
            SET price=?, value=?
            WHERE code=?
            """,
            (
                price,
                value,
                code
            )
        )

    await update.message.reply_text(
        f"✅ {code} updated.\n"
        f"Price: {price} WOR\n"
        f"Benefit: {value}"
    )


async def boostertoggle(update, context):

    if update.effective_user.id != ADMIN_ID:

        await update.message.reply_text(
            "⛔ Admin only."
        )

        return

    if (
        len(context.args) != 1
        or context.args[0] not in {
            "tap",
            "energy",
            "refill",
            "cobra"
        }
    ):

        await update.message.reply_text(
            "Usage: /boostertoggle tap"
        )

        return

    code = context.args[0]

    with db_connect() as db:

        row = db.execute(
            """
            SELECT active
            FROM boosters
            WHERE code=?
            """,
            (code,)
        ).fetchone()

        if not row:

            await update.message.reply_text(
                "Booster not found."
            )

            return

        active = 1 - row[0]

        db.execute(
            """
            UPDATE boosters
            SET active=?
            WHERE code=?
            """,
            (
                active,
                code
            )
        )

    await update.message.reply_text(
        f"{code} is now "
        f"{'ON' if active else 'OFF'}."
    )


# =========================================================
# CALLBACKS
# =========================================================

async def handle_callback(update, context):

    query = update.callback_query

    await query.answer()

    user = query.from_user

    user_id = user.id

    data = query.data

    ensure_user(
        user_id,
        user.username
    )

    if data == "home":

        await query.edit_message_text(
            "🐍 WORCOIN Main Menu",
            reply_markup=home_keyboard()
        )

        return

    if data == "balance":

        coins = get_balance(
            user_id,
            user.username
        )

        await query.edit_message_text(
            f"💰 Your Balance: {coins} WOR",
            reply_markup=InlineKeyboardMarkup([
                main_menu_button()
            ])
        )

        return

    if data == "boosters":

        await query.edit_message_text(
            "⚡ Booster Shop\nChoose a booster:",
            reply_markup=booster_keyboard()
        )

        return

    if data == "tasks":

        await query.edit_message_text(
            "📋 Available Tasks",
            reply_markup=task_keyboard(
                user_id
            )
        )

        return

    if data in {
        "noop",
        "already_claimed"
    }:

        if data == "already_claimed":

            await query.answer(
                "You already claimed this task.",
                show_alert=True
            )

        return

    if data == "tap":

        with db_connect() as db:

            row = db.execute(
                """
                SELECT coins,
                       tap_power,
                       energy_limit,
                       energy
                FROM users
                WHERE user_id=?
                """,
                (user_id,)
            ).fetchone()

            coins, power, energy_limit, energy = row

            if energy <= 0:

                await query.answer(
                    "No energy left.",
                    show_alert=True
                )

                return

            earned = max(
                1,
                power
            )

            db.execute(
                """
                UPDATE users
                SET coins=coins+?,
                    energy=energy-1
                WHERE user_id=?
                """,
                (
                    earned,
                    user_id
                )
            )

            balance = coins + earned

            energy_left = energy - 1

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    f"⛏️ TAP +{earned} WOR",
                    callback_data="tap"
                )
            ],
            [
                InlineKeyboardButton(
                    "📋 Tasks",
                    callback_data="tasks"
                ),
                InlineKeyboardButton(
                    "💰 Balance",
                    callback_data="balance"
                )
            ],
            main_menu_button()
        ])

        await query.edit_message_text(
            f"⛏️ Mining WORCOIN\n\n"
            f"🪙 Balance: {balance} WOR\n"
            f"⚡ Energy: "
            f"{energy_left}/{energy_limit}",
            reply_markup=keyboard
        )

        return

    if data.startswith("open:"):

        try:

            task_id = int(
                data.split(":", 1)[1]
            )

        except ValueError:

            await query.answer(
                "Invalid task.",
                show_alert=True
            )

            return

        with db_connect() as db:

            task = db.execute(
                """
                SELECT title,
                       url,
                       reward
                FROM tasks
                WHERE task_id=?
                AND active=1
                """,
                (task_id,)
            ).fetchone()

            claimed = db.execute(
                """
                SELECT 1
                FROM task_claims
                WHERE user_id=?
                AND task_id=?
                """,
                (
                    user_id,
                    task_id
                )
            ).fetchone()

        if not task:

            await query.answer(
                "Task unavailable.",
                show_alert=True
            )

            return

        if claimed:

            await query.answer(
                "Already claimed.",
                show_alert=True
            )

            return

        title, url, reward = task

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔗 Open Task",
                    url=url
                )
            ],
            [
                InlineKeyboardButton(
                    "✅ Verify & Claim",
                    callback_data=f"claim:{task_id}"
                )
            ],
            main_menu_button()
        ])

        await query.edit_message_text(
            f"📋 {title}\n"
            f"Reward: {reward} WOR\n\n"
            "Complete the task, then verify.",
            reply_markup=keyboard
        )

        return

    if data.startswith("claim:"):

        try:

            task_id = int(
                data.split(":", 1)[1]
            )

        except ValueError:

            await query.answer(
                "Invalid task.",
                show_alert=True
            )

            return

        with db_connect() as db:

            task = db.execute(
                """
                SELECT title,
                       chat_id,
                       reward
                FROM tasks
                WHERE task_id=?
                AND active=1
                """,
                (task_id,)
            ).fetchone()

            claimed = db.execute(
                """
                SELECT 1
                FROM task_claims
                WHERE user_id=?
                AND task_id=?
                """,
                (
                    user_id,
                    task_id
                )
            ).fetchone()

        if not task:

            await query.answer(
                "Task unavailable.",
                show_alert=True
            )

            return

        if claimed:

            await query.answer(
                "Already claimed.",
                show_alert=True
            )

            return

        title, chat_id, reward = task

        try:

            member = await context.bot.get_chat_member(
                chat_id=chat_id,
                user_id=user_id
            )

            if member.status not in (
                "member",
                "administrator",
                "creator"
            ):

                await query.answer(
                    "Please join the channel first.",
                    show_alert=True
                )

                return

        except Exception:

            log.exception(
                "Membership verification failed"
            )

            await query.answer(
                "Could not verify membership.",
                show_alert=True
            )

            return

        try:

            with db_connect() as db:

                db.execute(
                    """
                    INSERT INTO task_claims
                    (user_id, task_id)
                    VALUES (?, ?)
                    """,
                    (
                        user_id,
                        task_id
                    )
                )

                db.execute(
                    """
                    UPDATE users
                    SET coins=coins+?
                    WHERE user_id=?
                    """,
                    (
                        reward,
                        user_id
                    )
                )

                balance = db.execute(
                    """
                    SELECT coins
                    FROM users
                    WHERE user_id=?
                    """,
                    (user_id,)
                ).fetchone()[0]

        except sqlite3.IntegrityError:

            await query.answer(
                "Already claimed.",
                show_alert=True
            )

            return

        await query.edit_message_text(
            f"✅ Task completed: {title}\n"
            f"🎁 Reward: {reward} WOR\n"
            f"💰 New balance: {balance} WOR",
            reply_markup=InlineKeyboardMarkup([
                main_menu_button()
            ])
        )

        return

    if data.startswith("buy:"):

        code = data.split(
            ":",
            1
        )[1]

        with db_connect() as db:

            booster = db.execute(
                """
                SELECT title,
                       price,
                       value,
                       active
                FROM boosters
                WHERE code=?
                """,
                (code,)
            ).fetchone()

            if not booster or not booster[3]:

                await query.answer(
                    "Booster unavailable.",
                    show_alert=True
                )

                return

            title, price, value, _active = booster

            row = db.execute(
                """
                SELECT coins,
                       tap_power,
                       energy_limit,
                       energy
                FROM users
                WHERE user_id=?
                """,
                (user_id,)
            ).fetchone()

            coins, power, energy_limit, energy = row

            if coins < price:

                await query.answer(
                    "Not enough WOR coins.",
                    show_alert=True
                )

                return

            db.execute(
                """
                UPDATE users
                SET coins=coins-?
                WHERE user_id=?
                """,
                (
                    price,
                    user_id
                )
            )

            if code == "tap":

                db.execute(
                    """
                    UPDATE users
                    SET tap_power=tap_power+?
                    WHERE user_id=?
                    """,
                    (
                        value,
                        user_id
                    )
                )

                benefit = (
                    f"Tap power increased by {value}."
                )

            elif code == "energy":

                db.execute(
                    """
                    UPDATE users
                    SET energy_limit=energy_limit+?,
                        energy=MIN(
                            energy+?,
                            energy_limit+?
                        )
                    WHERE user_id=?
                    """,
                    (
                        value,
                        value,
                        value,
                        user_id
                    )
                )

                benefit = (
                    f"Energy limit increased by {value}."
                )

            elif code == "refill":

                db.execute(
                    """
                    UPDATE users
                    SET energy=energy_limit
                    WHERE user_id=?
                    """,
                    (user_id,)
                )

                benefit = "Energy refilled."

            elif code == "cobra":

                db.execute(
                    """
                    UPDATE users
                    SET tap_power=tap_power+?
                    WHERE user_id=?
                    """,
                    (
                        value,
                        user_id
                    )
                )

                benefit = (
                    f"Cobra power increased by {value}."
                )

            else:

                await query.answer(
                    "Unknown action.",
                    show_alert=True
                )

                return

            balance = db.execute(
                """
                SELECT coins
                FROM users
                WHERE user_id=?
                """,
                (user_id,)
            ).fetchone()[0]

        await query.edit_message_text(
            f"✅ Purchased: {title}\n"
            f"{benefit}\n"
            f"🪙 Spent: {price} WOR\n"
            f"💰 Balance: {balance} WOR",
            reply_markup=InlineKeyboardMarkup([
                main_menu_button()
            ])
        )

        return

    await query.answer(
        "Unknown action.",
        show_alert=True
    )


# =========================================================
# ERROR
# =========================================================

async def error_handler(update, context):

    log.error(
        "Unhandled bot error",
        exc_info=context.error
    )


# =========================================================
# MAIN
# =========================================================

def main():

    if not TOKEN:

        raise RuntimeError(
            "Missing TELEGRAM_BOT_TOKEN "
            "environment variable."
        )

    if not supabase_ready():

        log.warning(
            "Supabase environment variables "
            "are missing. API will not work."
        )

    init_db()

    Thread(
        target=run_health_server,
        daemon=True
    ).start()

    app = (
        Application
        .builder()
        .token(TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CommandHandler(
            "balance",
            balance_command
        )
    )

    app.add_handler(
        CommandHandler(
            "addtask",
            addtask
        )
    )

    app.add_handler(
        CommandHandler(
            "listtasks",
            listtasks
        )
    )

    app.add_handler(
        CommandHandler(
            "tasktoggle",
            tasktoggle
        )
    )

    app.add_handler(
        CommandHandler(
            "stats",
            stats
        )
    )

    app.add_handler(
        CommandHandler(
            "boosters",
            boosters_command
        )
    )

    app.add_handler(
        CommandHandler(
            "boosterset",
            boosterset
        )
    )

    app.add_handler(
        CommandHandler(
            "boostertoggle",
            boostertoggle
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            handle_callback
        )
    )

    app.add_error_handler(
        error_handler
    )

    log.info(
        "WORCOIN bot starting..."
    )

    app.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
