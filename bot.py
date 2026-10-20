import os
import json
import hmac
import hashlib
import time
import logging
from urllib.parse import parse_qsl
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes


# =========================
# CONFIG
# =========================

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("BOT_TOKEN")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

GAME_URL = (
    os.environ.get("GAME_URL")
    or os.environ.get("RENDER_EXTERNAL_URL")
    or "https://worcoin-mining-bot.onrender.com"
)

PORT = int(os.environ.get("PORT", "10000"))

CHANNEL_USERNAME = "@owlcoin137"

DAILY_REWARD = 100
CHANNEL_REWARD = 500

MAX_TAP_BATCH = 20
INITDATA_MAX_AGE = 86400

MAX_LEVEL = 5

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


logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

log = logging.getLogger("worcoin")


# =========================
# SUPABASE
# =========================

def sb(method, path, body=None, query=""):
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("Supabase environment variables are missing")

    url = SUPABASE_URL + path

    if query:
        url += "?" + query

    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": "Bearer " + SUPABASE_SERVICE_ROLE_KEY,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    if method.upper() in ("POST", "PATCH", "PUT"):
        headers["Prefer"] = "return=representation"

    data = None

    if body is not None:
        data = json.dumps(body).encode("utf-8")

    req = Request(
        url,
        data=data,
        headers=headers,
        method=method.upper(),
    )

    try:
        with urlopen(req, timeout=15) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else []

    except HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise RuntimeError(
            f"Supabase HTTP {e.code}: {detail}"
        )

    except URLError as e:
        raise RuntimeError(
            f"Supabase connection error: {e}"
        )


def rpc(name, args):
    result = sb(
        "POST",
        f"/rest/v1/rpc/{name}",
        body=args,
    )

    if isinstance(result, list):
        return result[0] if result else {}

    if isinstance(result, dict):
        return result

    return {}


# =========================
# TELEGRAM INIT DATA
# =========================

def validate_init_data(init_data):
    if not init_data or not TOKEN:
        return None

    try:
        params = dict(
            parse_qsl(
                init_data,
                keep_blank_values=True,
            )
        )

        received_hash = params.pop("hash", None)

        if not received_hash:
            return None

        auth_date = int(
            params.get("auth_date", "0")
        )

        if not auth_date:
            return None

        if abs(int(time.time()) - auth_date) > INITDATA_MAX_AGE:
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
            received_hash,
        ):
            return None

        user = json.loads(
            params.get("user", "{}")
        )

        if not user.get("id"):
            return None

        return user

    except Exception:
        return None


# =========================
# PROFILE
# =========================

def get_profile(uid):
    """
    IMPORTANT:
    select=* ব্যবহার করা হয়েছে।
    তাই refer_count-এর মতো কোনো optional
    column না থাকলেও profile load বন্ধ হবে না।
    """

    rows = sb(
        "GET",
        "/rest/v1/worcoin_profiles",
        query=(
            f"telegram_id=eq.{int(uid)}"
            "&limit=1"
        ),
    )

    return rows[0] if rows else None


def get_legacy_balance(uid):
    try:
        rows = sb(
            "GET",
            "/rest/v1/profiles",
            query=(
                "select=telegram_id,balance"
                f"&telegram_id=eq.{int(uid)}"
                "&limit=1"
            ),
        )

        if not rows:
            return 0

        return int(
            rows[0].get("balance") or 0
        )

    except Exception:
        return 0


def ensure_profile(user):
    uid = int(user["id"])

    profile = get_profile(uid)

    if profile:
        current_balance = int(
            profile.get("balance") or 0
        )

        if current_balance <= 0:
            legacy = get_legacy_balance(uid)

            if legacy > 0:
                try:
                    rows = sb(
                        "PATCH",
                        "/rest/v1/worcoin_profiles",
                        body={
                            "balance": legacy
                        },
                        query=(
                            f"telegram_id=eq.{uid}"
                        ),
                    )

                    if rows:
                        profile = rows[0]

                except Exception:
                    log.exception(
                        "Legacy balance recovery failed"
                    )

        return profile

    legacy = get_legacy_balance(uid)

    new_profile = {
        "telegram_id": uid,
        "username": user.get("username") or "",
        "firstname": user.get("first_name") or "",
        "balance": legacy,
        "mininglevel": 1,
        "boost_level": 1,
        "energy": 1000,
        "energy_level": 1,
    }

    rows = sb(
        "POST",
        "/rest/v1/worcoin_profiles",
        body=new_profile,
    )

    if rows:
        return rows[0]

    return new_profile


def public_profile(profile):
    if not profile:
        return None

    mining_level = int(
        profile.get("mininglevel") or 1
    )

    boost_level = int(
        profile.get("boost_level") or 1
    )

    energy_level = int(
        profile.get("energy_level") or 1
    )

    return {
        "telegram_id": int(
            profile.get("telegram_id") or 0
        ),

        "username": profile.get(
            "username"
        ) or "",

        "firstname": profile.get(
            "firstname"
        ) or "",

        "balance": int(
            profile.get("balance") or 0
        ),

        "mininglevel": mining_level,

        "boost_level": boost_level,

        "energy": int(
            profile.get("energy") or 0
        ),

        "energy_level": energy_level,

        # refer_count বাদ দেওয়া হয়েছে।
        # database-এ column না থাকলেও আর error হবে না।
        "refer_count": 0,

        "channel_claimed": bool(
            profile.get("channel_claimed") or False
        ),

        "last_daily_at": profile.get(
            "last_daily_at"
        ),

        "tap_reward": TAP_REWARD.get(
            boost_level,
            boost_level,
        ),

        "energy_capacity": ENERGY_CAPACITY.get(
            energy_level,
            1000,
        ),

        "mining_speed": MINING_SPEED.get(
            mining_level,
            mining_level,
        ),

        "next_boost_cost": BOOST_COST.get(
            boost_level
        ),

        "next_energy_cost": ENERGY_COST.get(
            energy_level
        ),

        "next_mining_cost": MINING_COST.get(
            mining_level
        ),
    }


# =========================
# TELEGRAM API
# =========================

def telegram_call(method, payload):
    if not TOKEN:
        raise RuntimeError(
            "Telegram bot token missing"
        )

    url = (
        f"https://api.telegram.org/"
        f"bot{TOKEN}/{method}"
    )

    req = Request(
        url,
        data=json.dumps(payload).encode(
            "utf-8"
        ),
        headers={
            "Content-Type":
                "application/json"
        },
        method="POST",
    )

    with urlopen(req, timeout=12) as response:
        result = json.loads(
            response.read().decode("utf-8")
        )

    if not result.get("ok"):
        raise RuntimeError(
            result.get(
                "description",
                "Telegram API error",
            )
        )

    return result.get("result")


def verify_channel_member(uid):
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

        status = member.get("status")

        if status in (
            "member",
            "administrator",
            "creator",
        ):
            return True, ""

        if (
            status == "restricted"
            and member.get("is_member")
        ):
            return True, ""

        return (
            False,
            "Join the WORCOIN channel first.",
        )

    except Exception:
        log.exception(
            "Channel verification failed"
        )

        return (
            False,
            "Membership verification failed. Make sure the bot is admin in the channel.",
        )


# =========================
# GAME ACTIONS
# =========================

def attach_profile(uid, result):
    if (
        isinstance(result, dict)
        and result.get("success")
    ):
        profile = get_profile(uid)

        if profile:
            result["profile"] = public_profile(
                profile
            )

    return result


def do_tap(uid, count):
    count = max(
        1,
        min(
            int(count or 1),
            MAX_TAP_BATCH,
        ),
    )

    result = rpc(
        "worcoin_tap",
        {
            "p_telegram_id": int(uid),
            "p_count": count,
        },
    )

    return attach_profile(
        uid,
        result,
    )


def do_daily(uid):
    result = rpc(
        "worcoin_daily",
        {
            "p_telegram_id": int(uid),
        },
    )

    return attach_profile(
        uid,
        result,
    )


def do_upgrade(uid, upgrade_type):
    if upgrade_type not in (
        "energy",
        "mining",
        "boost",
    ):
        return {
            "success": False,
            "message":
                "Invalid upgrade type.",
        }

    result = rpc(
        "worcoin_upgrade",
        {
            "p_telegram_id": int(uid),
            "p_type": upgrade_type,
        },
    )

    return attach_profile(
        uid,
        result,
    )


def do_channel_claim(uid):
    verified, message = verify_channel_member(
        uid
    )

    if not verified:
        return {
            "success": False,
            "message": message,
        }

    result = rpc(
        "worcoin_channel_claim",
        {
            "p_telegram_id": int(uid),
            "p_reward": CHANNEL_REWARD,
        },
    )

    return attach_profile(
        uid,
        result,
    )


def get_tasks():
    rows = sb(
        "GET",
        "/rest/v1/tasks",
        query=(
            "select=id,title,description,reward,task_url,is_active"
            "&is_active=eq.true"
            "&order=id"
        ),
    )

    return (
        rows
        if isinstance(rows, list)
        else []
    )


# =========================
# HTTP SERVER
# =========================

class APIHandler(BaseHTTPRequestHandler):

    server_version = "WORCOIN/2.0"

    def log_message(self, format, *args):
        return

    def cors(self):
        self.send_header(
            "Access-Control-Allow-Origin",
            "*",
        )

        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, X-Telegram-Init-Data",
        )

        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, OPTIONS",
        )

    def json_response(self, status, data):
        raw = json.dumps(
            data,
            ensure_ascii=False,
        ).encode("utf-8")

        self.send_response(status)

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8",
        )

        self.send_header(
            "Content-Length",
            str(len(raw)),
        )

        self.send_header(
            "Cache-Control",
            "no-store",
        )

        self.cors()

        self.end_headers()

        self.wfile.write(raw)

    def read_json(self):
        try:
            length = int(
                self.headers.get(
                    "Content-Length",
                    "0",
                )
            )

            if length <= 0:
                return {}

            return json.loads(
                self.rfile.read(length).decode(
                    "utf-8"
                )
            )

        except Exception:
            return {}

    def current_user(self):
        return validate_init_data(
            self.headers.get(
                "X-Telegram-Init-Data",
                "",
            )
        )

    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.end_headers()

    def do_GET(self):

        path = self.path.split(
            "?",
            1,
        )[0]

        # =====================
        # FRONTEND
        # =====================

        if path in (
            "/",
            "/index.html",
        ):

            index_path = os.path.join(
                os.path.dirname(
                    os.path.abspath(__file__)
                ),
                "index.html",
            )

            if not os.path.exists(
                index_path
            ):
                return self.json_response(
                    404,
                    {
                        "success": False,
                        "error":
                            "index.html not found",
                    },
                )

            try:
                with open(
                    index_path,
                    "rb",
                ) as f:
                    raw = f.read()

                self.send_response(200)

                self.send_header(
                    "Content-Type",
                    "text/html; charset=utf-8",
                )

                self.send_header(
                    "Content-Length",
                    str(len(raw)),
                )

                self.send_header(
                    "Cache-Control",
                    "no-store",
                )

                self.end_headers()

                self.wfile.write(raw)

                return

            except Exception as e:
                log.exception(
                    "Frontend error"
                )

                return self.json_response(
                    500,
                    {
                        "success": False,
                        "error": str(e),
                    },
                )

        # =====================
        # HEALTH
        # =====================

        if path == "/api/health":
            return self.json_response(
                200,
                {
                    "success": True,
                    "service":
                        "WORCOIN",
                    "version":
                        "2.0",
                },
            )

        # =====================
        # PROFILE
        # =====================

        if path == "/api/profile":

            user = self.current_user()

            if not user:
                return self.json_response(
                    401,
                    {
                        "success": False,
                        "error":
                            "Invalid or expired Telegram session.",
                    },
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
                            ),
                    },
                )

            except Exception as e:
                log.exception(
                    "Profile error"
                )

                return self.json_response(
                    500,
                    {
                        "success": False
