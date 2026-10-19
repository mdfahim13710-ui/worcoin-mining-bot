import os
import sqlite3
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN') or os.environ.get('BOT_TOKEN')
ADMIN_ID = 8744701224
DB_PATH = os.environ.get('DB_PATH', 'mining.db')
GAME_URL = 'https://worcoin-mining-bot.mdfahim13711.workers.dev'
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

DEFAULT_BOOSTERS = [
    ('tap', '⚡ Tap Multiplier', 100, 2, 1),
    ('energy', '🔋 Energy Limit', 150, 100, 1),
    ('refill', '🔄 Energy Refill', 50, 100, 1),
    ('cobra', '🐍 Cobra Power', 250, 1, 1),
]

def db_connect():
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.execute('PRAGMA foreign_keys=ON')
    return db

def init_db():
    with db_connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, username TEXT, coins INTEGER NOT NULL DEFAULT 0,
            tap_power INTEGER NOT NULL DEFAULT 1, energy_limit INTEGER NOT NULL DEFAULT 500,
            energy INTEGER NOT NULL DEFAULT 500)''')
        # Safely add columns when upgrading an existing database.
        cols = {r[1] for r in db.execute('PRAGMA table_info(users)')}
        for name, definition in [('tap_power','INTEGER NOT NULL DEFAULT 1'), ('energy_limit','INTEGER NOT NULL DEFAULT 500'), ('energy','INTEGER NOT NULL DEFAULT 500')]:
            if name not in cols:
                db.execute(f'ALTER TABLE users ADD COLUMN {name} {definition}')
        db.execute('''CREATE TABLE IF NOT EXISTS tasks (
            task_id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            chat_id TEXT NOT NULL, url TEXT NOT NULL, reward INTEGER NOT NULL,
            active INTEGER NOT NULL DEFAULT 1)''')
        db.execute('''CREATE TABLE IF NOT EXISTS task_claims (
            user_id INTEGER NOT NULL, task_id INTEGER NOT NULL,
            PRIMARY KEY (user_id, task_id))''')
        db.execute('''CREATE TABLE IF NOT EXISTS boosters (
            code TEXT PRIMARY KEY, title TEXT NOT NULL, price INTEGER NOT NULL,
            value INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1)''')
        db.execute('''CREATE TABLE IF NOT EXISTS booster_levels (
            user_id INTEGER NOT NULL, code TEXT NOT NULL, level INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, code))''')
        for code, title, price, value, active in DEFAULT_BOOSTERS:
            db.execute('INSERT OR IGNORE INTO boosters(code,title,price,value,active) VALUES(?,?,?,?,?)', (code,title,price,value,active))

def ensure_user(user_id, username=None):
    with db_connect() as db:
        db.execute('INSERT OR IGNORE INTO users(user_id,username) VALUES(?,?)', (user_id, username))
        if username:
            db.execute('UPDATE users SET username=? WHERE user_id=?', (username,user_id))
        return db.execute('SELECT coins,tap_power,energy_limit,energy FROM users WHERE user_id=?', (user_id,)).fetchone()

def get_balance(user_id, username=None):
    return ensure_user(user_id, username)[0]

def home_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('▶️ Play Worcoin', web_app=WebAppInfo(url=GAME_URL))],
        [InlineKeyboardButton('⚡ Booster Shop', callback_data='boosters')],
        [InlineKeyboardButton('⛏️ Tap to Mine', callback_data='tap')],
        [InlineKeyboardButton('📋 Tasks', callback_data='tasks'), InlineKeyboardButton('💰 Balance', callback_data='balance')],
    ])

def main_menu_button():
    return [InlineKeyboardButton('🏠 Main Menu', callback_data='home')]

def task_keyboard(user_id):
    with db_connect() as db:
        tasks = db.execute('SELECT task_id,title,reward FROM tasks WHERE active=1 ORDER BY task_id').fetchall()
        claimed = {r[0] for r in db.execute('SELECT task_id FROM task_claims WHERE user_id=?',(user_id,)).fetchall()}
    rows=[]
    for tid,title,reward in tasks:
        rows.append([InlineKeyboardButton(('✅ ' if tid in claimed else '🎁 ')+f'{title}'+(' — Claimed' if tid in claimed else f' (+{reward} WOR)'), callback_data='already_claimed' if tid in claimed else f'open:{tid}')])
    if not tasks: rows.append([InlineKeyboardButton('No tasks available yet', callback_data='noop')])
    rows.append(main_menu_button())
    return InlineKeyboardMarkup(rows)

def booster_keyboard():
    with db_connect() as db:
        rows = db.execute('SELECT code,title,price,value FROM boosters WHERE active=1 ORDER BY code').fetchall()
    buttons = [[InlineKeyboardButton(f'{title} · {price} WOR', callback_data=f'buy:{code}')] for code,title,price,value in rows]
    if not buttons: buttons=[[InlineKeyboardButton('No boosters available',callback_data='noop')]]
    buttons.append(main_menu_button())
    return InlineKeyboardMarkup(buttons)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user=update.effective_user; coins=get_balance(user.id,user.username)
    await update.message.reply_text(f'🐍 Welcome to WORCOIN!\n\n🪙 Your Balance: {coins} WOR\n\nPlay, mine, upgrade boosters, or complete tasks!',reply_markup=home_keyboard())

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user=update.effective_user; coins=get_balance(user.id,user.username)
    await update.message.reply_text(f'💰 Your Balance: {coins} WOR')

async def addtask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text('⛔ Admin only.'); return
    parts=[p.strip() for p in update.message.text.partition(' ')[2].split('|')]
    if len(parts)!=4:
        await update.message.reply_text('Format:\n/addtask @channel | Task title | https://t.me/channel | 100'); return
    chat_id,title,url,reward_text=parts
    if not url.startswith(('https://t.me/','https://telegram.me/')):
        await update.message.reply_text('Please provide a Telegram link.'); return
    try:
        reward=int(reward_text)
        if reward<=0: raise ValueError
    except ValueError:
        await update.message.reply_text('Reward must be a positive whole number.'); return
    with db_connect() as db:
        cur=db.execute('INSERT INTO tasks(title,chat_id,url,reward) VALUES(?,?,?,?)',(title,chat_id,url,reward)); tid=cur.lastrowid
    await update.message.reply_text(f'✅ Task added! ID: {tid}\nTitle: {title}\nReward: {reward} WOR')

async def listtasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id!=ADMIN_ID:
        await update.message.reply_text('⛔ Admin only.'); return
    with db_connect() as db: tasks=db.execute('SELECT task_id,title,reward,active FROM tasks ORDER BY task_id').fetchall()
    if not tasks: await update.message.reply_text('No tasks yet. Use /addtask.'); return
    await update.message.reply_text('📋 WORCOIN Tasks\n'+'\n'.join(f"#{i} {'ON' if a else 'OFF'} — {t} — {r} WOR" for i,t,r,a in tasks))

async def tasktoggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id!=ADMIN_ID:
        await update.message.reply_text('⛔ Admin only.'); return
    if len(context.args)!=1 or not context.args[0].isdigit():
        await update.message.reply_text('Usage: /tasktoggle 1'); return
    tid=int(context.args[0])
    with db_connect() as db:
        row=db.execute('SELECT active FROM tasks WHERE task_id=?',(tid,)).fetchone()
        if not row: await update.message.reply_text('Task not found.'); return
        active=1-row[0]; db.execute('UPDATE tasks SET active=? WHERE task_id=?',(active,tid))
    await update.message.reply_text(f"Task #{tid} is now {'ON' if active else 'OFF'}.")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id!=ADMIN_ID:
        await update.message.reply_text('⛔ Admin only.'); return
    with db_connect() as db:
        members=db.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        total=db.execute('SELECT COALESCE(SUM(coins),0) FROM users').fetchone()[0]
        tasks=db.execute('SELECT COUNT(*) FROM tasks WHERE active=1').fetchone()[0]
    await update.message.reply_text(f'🛠 WORCOIN Admin\nMembers: {members}\nTotal WOR: {total}\nActive tasks: {tasks}')

async def boosters_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user.id,update.effective_user.username)
    await update.message.reply_text('⚡ Booster Shop\nChoose a booster to purchase. Prices and benefits are configured by admin.',reply_markup=booster_keyboard())

async def boosterset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id!=ADMIN_ID:
        await update.message.reply_text('⛔ Admin only.'); return
    # /boosterset tap | price | value
    parts=[p.strip() for p in update.message.text.partition(' ')[2].split('|')]
    if len(parts)!=3 or parts[0] not in {'tap','energy','refill','cobra'}:
        await update.message.reply_text('Format: /boosterset tap | 100 | 2\nCodes: tap, energy, refill, cobra\nprice = WOR price; value = benefit for next purchase.'); return
    code=parts[0]
    try:
        price,value=int(parts[1]),int(parts[2])
        if price<0 or value<1: raise ValueError
    except ValueError:
        await update.message.reply_text('Price must be 0 or more; value must be a positive whole number.'); return
    with db_connect() as db: db.execute('UPDATE boosters SET price=?,value=? WHERE code=?',(price,value,code))
    await update.message.reply_text(f'✅ {code} updated. Price: {price} WOR; benefit: {value}.')

async def boostertoggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id!=ADMIN_ID:
        await update.message.reply_text('⛔ Admin only.'); return
    if len(context.args)!=1 or context.args[0] not in {'tap','energy','refill','cobra'}:
        await update.message.reply_text('Usage: /boostertoggle tap (codes: tap, energy, refill, cobra)'); return
    code=context.args[0]
    with db_connect() as db:
        row=db.execute('SELECT active FROM boosters WHERE code=?',(code,)).fetchone()
        if not row: await update.message.reply_text('Booster not found.'); return
        active=1-row[0]; db.execute('UPDATE boosters SET active=? WHERE code=?',(active,code))
    await update.message.reply_text(f"{code} is now {'ON' if active else 'OFF'}.")

async def callback_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; user=q.from_user; data=q.data or ''
    await q.answer()
    if data=='noop': return
    if data=='already_claimed':
        await q.answer('This reward was already claimed.',show_alert=True); return
    if data=='home':
        coins=get_balance(user.id,user.username)
        await q.edit_message_text(f'🐍 WORCOIN\n🪙 Balance: {coins} WOR',reply_markup=home_keyboard()); return
    if data=='balance':
        coins=get_balance(user.id,user.username); row=ensure_user(user.id,user.username)
        await q.edit_message_text(f'💰 Balance: {coins} WOR\n⚡ Tap power: {row[1]} WOR/tap\n🔋 Energy: {row[3]}/{row[2]}',reply_markup=home_keyboard()); return
    if data=='tap':
        with db_connect() as db:
            db.execute('INSERT OR IGNORE INTO users(user_id,username) VALUES(?,?)',(user.id,user.username))
            row=db.execute('SELECT coins,tap_power,energy_limit,energy FROM users WHERE user_id=?',(user.id,)).fetchone()
            coins,power,limit,energy=row
            if energy<=0:
                await q.edit_message_text(f'🔋 Energy finished!\nBalance: {coins} WOR',reply_markup=home_keyboard()); return
            db.execute('UPDATE users SET coins=coins+?, energy=energy-1 WHERE user_id=?',(power,user.id)); coins+=power; energy-=1
        await q.edit_message_text(f'🐍 WORCOIN Mining\n\n🪙 Balance: {coins} WOR\n✅ +{power} WOR\n🔋 Energy: {energy}/{limit}',reply_markup=home_keyboard()); return
    if data=='boosters':
        row=ensure_user(user.id,user.username)
        await q.edit_message_text(f'⚡ Booster Shop\nBalance: {row[0]} WOR\nTap a booster to buy it.',reply_markup=booster_keyboard()); return
    if data.startswith('buy:'):
        code=data.split(':',1)[1]
        with db_connect() as db:
            db.execute('BEGIN IMMEDIATE')
            b=db.execute('SELECT title,price,value,active FROM boosters WHERE code=?',(code,)).fetchone()
            if not b or not b[3]:
                db.rollback(); await q.edit_message_text('This booster is currently unavailable.',reply_markup=booster_keyboard()); return
            title,price,value,_=b
            db.execute('INSERT OR IGNORE INTO users(user_id,username) VALUES(?,?)',(user.id,user.username))
            coins=db.execute('SELECT coins FROM users WHERE user_id=?',(user.id,)).fetchone()[0]
            if coins<price:
                db.rollback(); await q.edit_message_text(f'Not enough WOR.\n{title} costs {price} WOR; your balance is {coins} WOR.',reply_markup=booster_keyboard()); return
            levelrow=db.execute('SELECT level FROM booster_levels WHERE user_id=? AND code=?',(user.id,code)).fetchone()
            level=levelrow[0] if levelrow else 0
            db.execute('UPDATE users SET coins=coins-? WHERE user_id=?',(price,user.id))
            db.execute('INSERT INTO booster_levels(user_id,code,level) VALUES(?,?,1) ON CONFLICT(user_id,code) DO UPDATE SET level=level+1',(user.id,code))
            if code=='tap': db.execute('UPDATE users SET tap_power=tap_power+? WHERE user_id=?',(value,user.id))
            elif code=='energy': db.execute('UPDATE users SET energy_limit=energy_limit+?, energy=MIN(energy+?, energy_limit+?) WHERE user_id=?',(value,value,value,user.id))
            elif code=='refill': db.execute('UPDATE users SET energy=energy_limit WHERE user_id=?',(user.id,))
            elif code=='cobra': db.execute('UPDATE users SET tap_power=tap_power+? WHERE user_id=?',(value,user.id))
            newcoins=db.execute('SELECT coins FROM users WHERE user_id=?',(user.id,)).fetchone()[0]
            row=db.execute('SELECT tap_power,energy,energy_limit FROM users WHERE user_id=?',(user.id,)).fetchone()
        await q.edit_message_text(f'✅ Booster purchased: {title}\nLevel: {level+1}\nSpent: {price} WOR\nBalance: {newcoins} WOR\nTap power: {row[0]} WOR/tap\nEnergy: {row[1]}/{row[2]}',reply_markup=booster_keyboard()); return
    if data=='tasks':
        await q.edit_message_text('📋 Tasks\n\nOpen a task, join its Telegram channel/group, then verify membership to claim the reward.',reply_markup=task_keyboard(user.id)); return
    if data.startswith('open:'):
        tid=int(data.split(':')[1])
        with db_connect() as db:
            task=db.execute('SELECT title,chat_id,url,reward FROM tasks WHERE task_id=? AND active=1',(tid,)).fetchone()
            claimed=db.execute('SELECT 1 FROM task_claims WHERE user_id=? AND task_id=?',(user.id,tid)).fetchone()
        if not task or claimed:
            await q.edit_message_text('Task unavailable or already claimed.',reply_markup=task_keyboard(user.id)); return
        title,chat_id,url,reward=task
        await q.edit_message_text(f'📋 {title}\n🎁 Reward: {reward} WOR\n\n1. Join the channel/group.\n2. Return and press Verify Join.\n3. Claim your reward.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('📢 Join',url=url)],[InlineKeyboardButton('🔎 Verify Join',callback_data=f'verify:{tid}')],[InlineKeyboardButton('⬅️ Tasks',callback_data='tasks')]])); return
    if data.startswith(('verify:','claim:')):
        action,tidtext=data.split(':'); tid=int(tidtext)
        with db_connect() as db:
            task=db.execute('SELECT title,chat_id,url,reward FROM tasks WHERE task_id=? AND active=1',(tid,)).fetchone()
            claimed=db.execute('SELECT 1 FROM task_claims WHERE user_id=? AND task_id=?',(user.id,tid)).fetchone()
        if not task or claimed:
            await q.edit_message_text('Task unavailable or already claimed.',reply_markup=task_keyboard(user.id)); return
        title,chat_id,url,reward=task
        try:
            member=await context.bot.get_chat_member(chat_id,user.id)
            is_member=member.status in ('member','administrator','creator') or (member.status=='restricted' and getattr(member,'is_member',False))
        except TelegramError:
            log.exception('Join verification error')
            await q.edit_message_text('Verification failed. Please make the bot an admin in the channel/group, then try again.',reply_markup=task_keyboard(user.id)); return
        if not is_member:
            await q.edit_message_text('Please join the channel/group first, then verify.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('📢 Join',url=url)],[InlineKeyboardButton('🔎 Verify Join',callback_data=f'verify:{tid}')],[InlineKeyboardButton('⬅️ Tasks',callback_data='tasks')]])); return
        if action=='verify':
            await q.edit_message_text(f'✅ Membership verified!\n\n{title}\n🎁 Reward: {reward} WOR',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🎁 Claim Reward',callback_data=f'claim:{tid}')],[InlineKeyboardButton('⬅️ Tasks',callback_data='tasks')]])); return
        with db_connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO users(user_id,username) VALUES(?,?)',(user.id,user.username))
            cur=db.execute('INSERT OR IGNORE INTO task_claims(user_id,task_id) VALUES(?,?)',(user.id,tid))
            if cur.rowcount==0:
                db.rollback(); await q.edit_message_text('Reward already claimed.',reply_markup=task_keyboard(user.id)); return
            db.execute('UPDATE users SET coins=coins+? WHERE user_id=?',(reward,user.id))
            coins=db.execute('SELECT coins FROM users WHERE user_id=?',(user.id,)).fetchone()[0]
        await q.edit_message_text(f'🎉 Claim successful!\n✅ +{reward} WOR\n🪙 New balance: {coins} WOR',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('📋 More Tasks',callback_data='tasks')],main_menu_button()])); return

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg='User commands: /start, /balance, /boosters\nAdmin commands: /addtask, /listtasks, /tasktoggle ID, /stats, /boosterset code | price | value, /boostertoggle code'
    await update.message.reply_text(msg)

def main():
    if not TOKEN: raise RuntimeError('Set TELEGRAM_BOT_TOKEN in hosting environment variables.')
    init_db()
    app=Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler('start',start)); app.add_handler(CommandHandler('balance',balance_command)); app.add_handler(CommandHandler('boosters',boosters_command)); app.add_handler(CommandHandler('help',help_command))
    app.add_handler(CommandHandler('stats',stats)); app.add_handler(CommandHandler('addtask',addtask)); app.add_handler(CommandHandler('listtasks',listtasks)); app.add_handler(CommandHandler('tasktoggle',tasktoggle)); app.add_handler(CommandHandler('boosterset',boosterset)); app.add_handler(CommandHandler('boostertoggle',boostertoggle)); app.add_handler(CallbackQueryHandler(callback_buttons))
    log.info('WORCOIN bot starting...'); app.run_polling()

if __name__=='__main__': main()
