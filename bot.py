import os
import json
import hmac
import hashlib
import time
from urllib.parse import parse_qsl
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import logging

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

TOKEN = (
    os.environ.get("TELEGRAM_BOT_TOKEN")
    or os.environ.get("BOT_TOKEN")
)

SUPABASE_URL = os.environ.get(
    "SUPABASE_URL",
    ""
).rstrip("/")

SUPABASE_SERVICE_ROLE_KEY = os.environ.get(
    "SUPABASE_SERVICE_ROLE_KEY",
    ""
)

GAME_URL = (
    os.environ.get("GAME_URL")
    or os.environ.get("RENDER_EXTERNAL_URL")
    or "https://worcoin-bot.onrender.com"
)

PORT = int(
    os.environ.get("PORT", "10000")
)

CHANNEL_USERNAME = "@owlcoin137"

DAILY_REWARD = 100
CHANNEL_REWARD = 500

MAX_TAP_BATCH = 20
INITDATA_MAX_AGE = 86400

MAX_LEVEL = 5


# =========================================================
# GAME SETTINGS
# =========================================================

TAP_REWARD = {
    1: 1,
    2: 2,
    3: 3,
    4: 4,
    5: 5,
}

ENERGY_CAPACITY = {
    1: 1000,
    2: 2000,
    3: 4000,
    4: 8000,
    5: 16000,
}

MINING_SPEED = {
    1: 1,
    2: 2,
    3: 3,
    4: 4,
    5: 5,
}

BOOST_COST = {
    1: 2000,
    2: 6000,
    3: 12000,
    4: 20000,
}

ENERGY_COST = {
    1: 4000,
    2: 8000,
    3: 16000,
    4: 32000,
}

MINING_COST = {
    1: 3000,
    2: 6000,
    3: 12000,
    4: 24000,
}


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=os.environ.get(
        "LOG_LEVEL",
        "INFO"
    ),
    format=(
        "%(asctime)s "
        "%(levelname)s "
        "%(name)s: %(message)s"
    ),
)

log = logging.getLogger("worcoin")


# =========================================================
# SUPABASE
# =========================================================

def sb(
    method,
    path,
    body=None,
    query=""
):
    if not SUPABASE_URL:
        raise RuntimeError(
            "SUPABASE_URL is missing"
        )

    if not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError(
            "SUPABASE_SERVICE_ROLE_KEY is missing"
        )

    url = SUPABASE_URL + path

    if query:
        url += "?" + query

    headers = {
        "apikey":
            SUPABASE_SERVICE_ROLE_KEY,

        "Authorization":
            "Bearer "
            + SUPABASE_SERVICE_ROLE_KEY,

        "Accept":
            "application/json",

        "Content-Type":
            "application/json",
    }

    if method.upper() in (
        "POST",
        "PATCH",
        "PUT",
    ):
        headers["Prefer"] = (
            "return=representation"
        )

    data = None

    if body is not None:
        data = json.dumps(
            body,
            ensure_ascii=False
        ).encode("utf-8")

    request = Request(
        url,
        data=data,
        headers=headers,
        method=method.upper(),
    )

    try:
        with urlopen(
            request,
            timeout=20
        ) as response:

            raw = response.read().decode(
                "utf-8"
            )

            if not raw:
                return []

            return json.loads(raw)

    except HTTPError as e:

        detail = e.read().decode(
            errors="replace"
        )

        raise RuntimeError(
            "Supabase HTTP "
            f"{e.code}: {detail}"
        )

    except URLError as e:

        raise RuntimeError(
            "Supabase connection error: "
            f"{e}"
        )


def rpc(
    name,
    args
):
    result = sb(
        "POST",
        "/rest/v1/rpc/" + name,
        body=args,
    )

    if isinstance(
        result,
        list
    ):
        return (
            result[0]
            if result
            else {}
        )

    if isinstance(
        result,
        dict
    ):
        return result

    return {}


# =========================================================
# TELEGRAM WEB APP SECURITY
# =========================================================

def validate_init_data(
    init_data
):
    if not init_data:
        return None

    if not TOKEN:
        return None

    try:

        params = dict(
            parse_qsl(
                init_data,
                keep_blank_values=True
            )
        )

        received_hash = params.pop(
            "hash",
            None
        )

        if not received_hash:
            return None

        auth_date = int(
            params.get(
                "auth_date",
                "0"
            )
        )

        if not auth_date:
            return None

        age = (
            int(time.time())
            - auth_date
        )

        if age > INITDATA_MAX_AGE:
            return None

        if age < -60:
            return None

        data_check_string = "\n".join(
            f"{key}={params[key]}"
            for key in sorted(params)
        )

        secret_key = hmac.new(
            b"WebAppData",
            TOKEN.encode(),
            hashlib.sha256,
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(
            calculated_hash,
            received_hash
        ):
            return None

        user_raw = params.get(
            "user",
            "{}"
        )

        user = json.loads(
            user_raw
        )

        if not user.get("id"):
            return None

        return user

    except Exception:

        log.exception(
            "Telegram initData validation failed"
        )

        return None


# =========================================================
# PROFILE
# =========================================================

def get_profile(
    uid
):
    rows = sb(
        "GET",
        "/rest/v1/worcoin_profiles",
        query=(
            "select="
            "telegram_id,"
            "username,"
            "firstname,"
            "balance,"
            "mininglevel,"
            "boost_level,"
            "refer_count,"
            "energy,"
            "energy_level,"
            "last_regen_at,"
            "last_daily_at,"
            "channel_claimed"
            f"&telegram_id=eq.{int(uid)}"
            "&limit=1"
        ),
    )

    return (
        rows[0]
        if rows
        else None
    )


def get_legacy_balance(
    uid
):
    try:

        rows = sb(
            "GET",
            "/rest/v1/profiles",
            query=(
                "select="
                "telegram_id,"
                "balance,"
                "username"
                f"&telegram_id=eq.{int(uid)}"
                "&limit=1"
            ),
        )

        if not rows:
            return None

        return int(
            rows[0].get(
                "balance"
            ) or 0
        )

    except Exception:

        return None


def ensure_profile(
    user
):
    uid = int(
        user["id"]
    )

    profile = get_profile(
        uid
    )

    if profile:

        if (
            int(
                profile.get(
                    "balance"
                ) or 0
            ) == 0
        ):

            legacy = (
                get_legacy_balance(
                    uid
                )
            )

            if legacy and legacy > 0:

                try:

                    rows = sb(
                        "PATCH",
                        "/rest/v1/worcoin_profiles",
                        body={
                            "balance":
                                legacy
                        },
                        query=(
                            f"telegram_id=eq.{uid}"
                        ),
                    )

                    if rows:
                        profile = rows[0]
                    else:
                        profile[
                            "balance"
                        ] = legacy

                except Exception:

                    profile[
                        "balance"
                    ] = legacy

        return profile

    legacy = (
        get_legacy_balance(
            uid
        )
        or 0
    )

    body = {
        "telegram_id":
            uid,

        "username":
            user.get(
                "username"
            ) or "",

        "firstname":
            user.get(
                "first_name"
            ) or "",

        "balance":
            legacy,

        "mininglevel":
            1,

        "boost_level":
            1,

        "refer_count":
            0,

        "energy":
            1000,

        "energy_level":
            1,
    }

    rows = sb(
        "POST",
        "/rest/v1/worcoin_profiles",
        body=body,
    )

    return (
        rows[0]
        if rows
        else body
    )


# =========================================================
# PUBLIC PROFILE
# =========================================================

def public_profile(
    profile
):
    if not profile:
        return None

    mining_level = max(
        1,
        min(
            MAX_LEVEL,
            int(
                profile.get(
                    "mininglevel"
                ) or 1
            )
        )
    )

    boost_level = max(
        1,
        min(
            MAX_LEVEL,
            int(
                profile.get(
                    "boost_level"
                ) or 1
            )
        )
    )

    energy_level = max(
        1,
        min(
            MAX_LEVEL,
            int(
                profile.get(
                    "energy_level"
                ) or 1
            )
        )
    )

    return {
        "telegram_id":
            int(
                profile.get(
                    "telegram_id"
                ) or 0
            ),

        "username":
            profile.get(
                "username"
            ) or "",

        "firstname":
            profile.get(
                "firstname"
            ) or "",

        "balance":
            int(
                profile.get(
                    "balance"
                ) or 0
            ),

        "mininglevel":
            mining_level,

        "boost_level":
            boost_level,

        "energy":
            int(
                profile.get(
                    "energy"
                ) or 0
            ),

        "energy_level":
            energy_level,

        "refer_count":
            int(
                profile.get(
                    "refer_count"
                ) or 0
            ),

        "channel_claimed":
            bool(
                profile.get(
                    "channel_claimed"
                ) or False
            ),

        "last_daily_at":
            profile.get(
                "last_daily_at"
            ),

        "tap_reward":
            TAP_REWARD.get(
                boost_level,
                1
            ),

        "energy_capacity":
            ENERGY_CAPACITY.get(
                energy_level,
                1000
            ),

        "mining_speed":
            MINING_SPEED.get(
                mining_level,
                1
            ),

        "next_boost_cost":
            BOOST_COST.get(
                boost_level
            ),

        "next_energy_cost":
            ENERGY_COST.get(
                energy_level
            ),

        "next_mining_cost":
            MINING_COST.get(
                mining_level
            ),
    }


# =========================================================
# TELEGRAM API
# =========================================================

def telegram_call(
    method,
    payload
):
    if not TOKEN:
        raise RuntimeError(
            "Telegram bot token missing"
        )

    url = (
        "https://api.telegram.org/"
        f"bot{TOKEN}/{method}"
    )

    request = Request(
        url,
        data=json.dumps(
            payload
        ).encode("utf-8"),
        headers={
            "Content-Type":
                "application/json"
        },
        method="POST",
    )

    with urlopen(
        request,
        timeout=15
    ) as response:

        result = json.loads(
            response.read().decode(
                "utf-8"
            )
        )

    if not result.get(
        "ok"
    ):
        raise RuntimeError(
            result.get(
                "description",
                "Telegram API error"
            )
        )

    return result.get(
        "result"
    )


def verify_channel_member(
    uid
):
    try:

        member = telegram_call(
            "getChatMember",
            {
                "chat_id":
                    CHANNEL_USERNAME,

                "user_id":
                    int(uid),
            },
        )

        status = member.get(
            "status"
        )

        if status in (
            "member",
            "administrator",
            "creator",
        ):
            return True, ""

        if (
            status == "restricted"
            and member.get(
                "is_member"
            )
        ):
            return True, ""

        return (
            False,
            "Join the channel first."
        )

    except Exception:

        log.exception(
            "Channel verification failed"
        )

        return (
            False,
            "Membership verification failed."
        )


# =========================================================
# GAME ACTIONS
# =========================================================

def attach_profile(
    uid,
    result
):
    if not isinstance(
        result,
        dict
    ):
        result = {
            "success": False,
            "message":
                "Invalid server response."
        }

    if result.get(
        "success"
    ):
        profile = get_profile(
            uid
        )

        if profile:
            result[
                "profile"
            ] = public_profile(
                profile
            )

    return result


def do_tap(
    uid,
    count
):
    try:
        count = int(
            count or 1
        )
    except Exception:
        count = 1

    count = max(
        1,
        min(
            count,
            MAX_TAP_BATCH
        )
    )

    return attach_profile(
        uid,
        rpc(
            "worcoin_tap",
            {
                "p_telegram_id":
                    int(uid),

                "p_count":
                    count,
            }
        )
    )


def do_daily(
    uid
):
    return attach_profile(
        uid,
        rpc(
            "worcoin_daily",
            {
                "p_telegram_id":
                    int(uid)
            }
        )
    )


def do_upgrade(
    uid,
    upgrade_type
):
    upgrade_type = str(
        upgrade_type
    ).lower().strip()

    if upgrade_type not in (
        "energy",
        "mining",
        "boost",
    ):
        return {
            "success": False,
            "message":
                "Invalid upgrade type."
        }

    return attach_profile(
        uid,
        rpc(
            "worcoin_upgrade",
            {
                "p_telegram_id":
                    int(uid),

                "p_type":
                    upgrade_type,
            }
        )
    )


def do_channel_claim(
    uid
):
    verified, message = (
        verify_channel_member(
            uid
        )
    )

    if not verified:
        return {
            "success": False,
            "message": message
        }

    return attach_profile(
        uid,
        rpc(
            "worcoin_channel_claim",
            {
                "p_telegram_id":
                    int(uid),

                "p_reward":
                    CHANNEL_REWARD,
            }
        )
    )


# =========================================================
# TASKS
# =========================================================

def get_tasks():

    rows = sb(
        "GET",
        "/rest/v1/tasks",
        query=(
            "select="
            "id,"
            "title,"
            "description,"
            "reward,"
            "task_url,"
            "is_active"
            "&is_active=eq.true"
            "&order=id"
        ),
    )

    return (
        rows
        if isinstance(
            rows,
            list
        )
        else []
    )


# =========================================================
# HTTP API
# =========================================================

class APIHandler(
    BaseHTTPRequestHandler
):

    server_version = (
        "WORCOIN/2.0"
    )

    def log_message(
        self,
        format,
        *args
    ):
        log.info(
            "%s - %s",
            self.address_string(),
            format % args
        )

    def cors(self):

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

    def json_response(
        self,
        status,
        payload
    ):
        raw = json.dumps(
            payload,
            ensure_ascii=False
        ).encode("utf-8")

        self.send_response(
            status
        )

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )

        self.send_header(
            "Content-Length",
            str(len(raw))
        )

        self.send_header(
            "Cache-Control",
            "no-store"
        )

        self.cors()

        self.end_headers()

        self.wfile.write(
            raw
        )

    def read_json(
        self
    ):
        try:

            length = int(
                self.headers.get(
                    "Content-Length",
                    "0"
                )
            )

            if length <= 0:
                return {}

            raw = self.rfile.read(
                length
            ).decode("utf-8")

            data = json.loads(
                raw
            )

            return (
                data
                if isinstance(
                    data,
                    dict
                )
                else {}
            )

        except Exception:
            return {}

    def current_user(
        self
    ):
        return validate_init_data(
            self.headers.get(
                "X-Telegram-Init-Data",
                ""
            )
        )

    def do_OPTIONS(
        self
    ):
        self.send_response(
            204
        )

        self.cors()

        self.end_headers()

    # -----------------------------------------------------
    # GET
    # -----------------------------------------------------

    def do_GET(
        self
    ):

        path = self.path.split(
            "?",
            1
        )[0]

        # Logo
        if path == "/wor-tap-logo.png":

            image_path = os.path.join(
                os.path.dirname(
                    os.path.abspath(
                        __file__
                    )
                ),
                "wor-tap-logo.png"
            )

            if not os.path.exists(
                image_path
            ):
                return self.json_response(
                    404,
                    {
                        "success": False,
                        "error":
                            "Tap logo not found"
                    }
                )

            with open(
                image_path,
                "rb"
            ) as file:
                raw = file.read()

            self.send_response(
                200
            )

            self.send_header(
                "Content-Type",
                "image/png"
            )

            self.send_header(
                "Content-Length",
                str(len(raw))
            )

            self.send_header(
                "Cache-Control",
                "no-store"
            )

            self.end_headers()

            self.wfile.write(
                raw
            )

            return

        # Frontend
        if path in (
            "/",
            "/index.html"
        ):

            index_path = os.path.join(
                os.path.dirname(
                    os.path.abspath(
                        __file__
                    )
                ),
                "index.html"
            )

            if os.path.exists(
                index_path
            ):

                with open(
                    index_path,
                    "rb"
                ) as file:
                    raw = file.read()

                self.send_response(
                    200
                )

                self.send_header(
                    "Content-Type",
                    "text/html; charset=utf-8"
                )

                self.send_header(
                    "Content-Length",
                    str(len(raw))
                )

                self.send_header(
                    "Cache-Control",
                    "no-store"
                )

                self.cors()

                self.end_headers()

                self.wfile.write(
                    raw
                )

                return

            return self.json_response(
                500,
                {
                    "success": False,
                    "error":
                        "index.html not found"
                }
            )

        # Health
        if path == "/api/health":

            return self.json_response(
                200,
                {
                    "success": True,
                    "service":
                        "WORCOIN",
                    "version":
                        "2.0"
                }
            )

        # Profile
        if path == "/api/profile":

            user = self.current_user()

            if not user:
                return self.json_response(
                    401,
                    {
                        "success": False,
                        "error":
                            "Invalid or expired Telegram session."
                    }
                )

            try:

                profile = ensure_profile(
                    user
                )

                return self.json_response(
                    200,
                    {
                        "success": True,
                        "profile":
                            public_profile(
                                profile
                            )
                    }
                )

            except Exception as e:

                log.exception(
                    "profile"
                )

                return self.json_response(
                    500,
                    {
                        "success": False,
                        "error":
                            str(e)
                    }
                )

        # Tasks
        if path == "/api/tasks":

            try:

                return self.json_response(
                    200,
                    {
                        "success": True,
                        "tasks":
                            get_tasks()
                    }
                )

            except Exception as e:

                log.exception(
                    "tasks"
                )

                return self.json_response(
                    500,
                    {
                        "success": False,
                        "error":
                            str(e)
                    }
                )

        # Rank
        if path == "/api/rank":

            try:

                rows = sb(
                    "GET",
                    "/rest/v1/worcoin_profiles",
                    query=(
                        "select="
                        "username,"
                        "firstname,"
                        "balance,"
                        "mininglevel,"
                        "boost_level"
                        "&order=balance.desc"
                        "&limit=100"
                    )
                )

                ranking = []

                for position, row in enumerate(
                    rows or [],
                    1
                ):

                    ranking.append(
                        {
                            "rank":
                                position,

                            "username":
                                row.get(
                                    "username"
                                ) or "",

                            "firstname":
                                row.get(
                                    "firstname"
                                ) or "",

                            "balance":
                                int(
                                    row.get(
                                        "balance"
                                    ) or 0
                                ),

                            "mininglevel":
                                int(
                                    row.get(
                                        "mininglevel"
                                    ) or 1
                                ),

                            "boost_level":
                                int(
                                    row.get(
                                        "boost_level"
                                    ) or 1
                                )
                        }
                    )

                return self.json_response(
                    200,
                    {
                        "success": True,
                        "count":
                            len(ranking),
                        "ranking":
                            ranking
                    }
                )

            except Exception as e:

                log.exception(
                    "rank"
                )

                return self.json_response(
                    500,
                    {
                        "success": False,
                        "error":
                            str(e)
                    }
                )

        return self.json_response(
            404,
            {
                "success": False,
                "error":
                    "Not found"
            }
        )

    # -----------------------------------------------------
    # POST
    # -----------------------------------------------------

    def do_POST(
        self
    ):

        path = self.path.split(
            "?",
            1
        )[0]

        user = self.current_user()

        if not user:

            return self.json_response(
                401,
                {
                    "success": False,
                    "error":
                        "Invalid or expired Telegram session."
                }
            )

        uid = int(
            user["id"]
        )

        body = self.read_json()

        try:

            if path == "/api/profile":

                profile = ensure_profile(
                    user
                )

                return self.json_response(
                    200,
                    {
                        "success": True,
                        "profile":
                            public_profile(
                                profile
                            )
                    }
                )

            if path == "/api/tap":

                result = do_tap(
                    uid,
                    body.get(
                        "count",
                        1
                    )
                )

                return self.json_response(
                    200,
                    result
                )

            if path == "/api/daily":

                result = do_daily(
                    uid
                )

                return self.json_response(
                    200,
                    result
                )

            if path == "/api/upgrade":

                result = do_upgrade(
                    uid,
                    body.get(
                        "type",
                        ""
                    )
                )

                return self.json_response(
                    200,
                    result
                )

            if path == "/api/channel-claim":

                result = do_channel_claim(
                    uid
                )

                return self.json_response(
                    200,
                    result
                )

            return self.json_response(
                404,
                {
                    "success": False,
                    "error":
                        "Not found"
                }
            )

        except Exception as e:

            log.exception(
                "API request failed"
            )

            return self.json_response(
                500,
                {
                    "success": False,
                    "error":
                        str(e)
                }
            )


# =========================================================
# HTTP SERVER
# =========================================================

def run_http():

    server = ThreadingHTTPServer(
        (
            "0.0.0.0",
            PORT
        ),
        APIHandler
    )

    log.info(
        "WORCOIN API listening on port %s",
        PORT
    )

    server.serve_forever()


# =========================================================
# TELEGRAM COMMANDS
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    keyboard = [
        [
            InlineKeyboardButton(
                "▶ PLAY WORCOIN",
                web_app=WebAppInfo(
                    url=GAME_URL
                )
            )
        ],
        [
            InlineKeyboardButton(
                "🎯 Tasks",
                callback_data="tasks"
            ),
            InlineKeyboardButton(
                "💰 Balance",
                callback_data="balance"
            )
        ]
    ]

    await update.message.reply_text(
        "WORCOIN Mining\n\n"
        "Tap, complete tasks and upgrade your mining power.",
        reply_markup=
            InlineKeyboardMarkup(
                keyboard
            )
    )


async def balance_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    uid = update.effective_user.id

    try:

        profile = get_profile(
            uid
        )

        if not profile:

            await update.message.reply_text(
                "Open WORCOIN first to create your profile."
            )

            return

        p = public_profile(
            profile
        )

        await update.message.reply_text(
            f"💰 Balance: "
            f"{p['balance']:,} WOR\n"
            f"⚡ Tap: "
            f"+{p['tap_reward']} WOR\n"
            f"⛏️ Mining Level: "
            f"{p['mininglevel']}\n"
            f"🚀 Boost Level: "
            f"{p['boost_level']}\n"
            f"🔋 Energy: "
            f"{p['energy']:,}/"
            f"{p['energy_capacity']:,}"
        )

    except Exception:

        log.exception(
            "balance command"
        )

        await update.message.reply_text(
            "Could not load your balance right now."
        )


async def tasks_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    keyboard = [
        [
            InlineKeyboardButton(
                "🎯 OPEN TASKS",
                web_app=WebAppInfo(
                    url=GAME_URL
                )
            )
        ]
    ]

    await update.message.reply_text(
        "🎯 Tasks\n\n"
        f"🎁 Daily Reward: "
        f"+{DAILY_REWARD} WOR\n"
        f"📣 Channel Join & Claim: "
        f"+{CHANNEL_REWARD} WOR",
        reply_markup=
            InlineKeyboardMarkup(
                keyboard
            )
    )


async def button_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    if query.data == "balance":

        uid = query.from_user.id

        try:

            profile = get_profile(
                uid
            )

            if not profile:

                await query.message.reply_text(
                    "Open WORCOIN first."
                )

                return

            p = public_profile(
                profile
            )

            await query.message.reply_text(
                f"💰 "
                f"{p['balance']:,} WOR\n"
                f"⚡ +"
                f"{p['tap_reward']} "
                f"WOR/tap\n"
                f"🔋 "
                f"{p['energy']:,}/"
                f"{p['energy_capacity']:,}"
            )

        except Exception:

            log.exception(
                "balance callback"
            )

            await query.message.reply_text(
                "Could not load balance."
            )

    elif query.data == "tasks":

        await tasks_command(
            update,
            context
        )


# =========================================================
# MAIN
# =========================================================

def main():

    if not TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing"
        )

    if not SUPABASE_URL:
        raise RuntimeError(
            "SUPABASE_URL is missing"
        )

    if not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError(
            "SUPABASE_SERVICE_ROLE_KEY is missing"
        )

    Thread(
        target=run_http,
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
            start_command
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
            "tasks",
            tasks_command
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            button_callback
        )
    )

    # Telegram Mini App menu button
    try:

        telegram_call(
            "setChatMenuButton",
            {
                "menu_button": {
                    "type":
                        "web_app",

                    "text":
                        "▶ PLAY WORCOIN",

                    "web_app": {
                        "url":
                            GAME_URL
                    }
                }
            }
        )

        log.info(
            "WORCOIN Telegram menu button set: %s",
            GAME_URL
        )

    except Exception:

        log.exception(
            "Could not set Telegram menu button"
        )

    log.info(
        "WORCOIN bot starting"
    )

    log.info(
        "Mini App URL: %s",
        GAME_URL
    )

    app.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
