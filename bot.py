import os,json,hmac,hashlib,time,logging
from urllib.parse import parse_qsl
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from threading import Thread
from telegram import Update,InlineKeyboardButton,InlineKeyboardMarkup,WebAppInfo
from telegram.ext import Application,CommandHandler,ContextTypes

TOKEN=os.getenv('TELEGRAM_BOT_TOKEN') or os.getenv('BOT_TOKEN')
SUPA=os.getenv('SUPABASE_URL','').rstrip('/')
KEY=os.getenv('SUPABASE_SERVICE_ROLE_KEY','')
GAME_URL=os.getenv('GAME_URL') or os.getenv('RENDER_EXTERNAL_URL') or 'https://worcoin-bot.onrender.com'
PORT=int(os.getenv('PORT','10000'))
CHANNEL='@owlcoin137'; CHANNEL_REWARD=1000; DAILY_REWARD=100; REFERRAL_REWARD=500; MAX_TAP=20; MAX_LEVEL=5
logging.basicConfig(level=os.getenv('LOG_LEVEL','INFO'),format='%(asctime)s %(levelname)s %(message)s');log=logging.getLogger('worcoin')

TAP={1:1,2:2,3:3,4:4,5:5}; ENERGY={1:1000,2:2000,3:4000,4:8000,5:16000}; REGEN={1:1,2:2,3:3,4:4,5:5}
BOOST_COST={1:2000,2:6000,3:12000,4:20000}; ENERGY_COST={1:4000,2:8000,3:16000,4:32000}; REGEN_COST={1:5000,2:10000,3:20000,4:40000}

def sb(method,path,body=None,query=''):
    if not SUPA or not KEY: raise RuntimeError('Supabase environment variables are missing')
    u=SUPA+path+('?' + query if query else '')
    h={'apikey':KEY,'Authorization':'Bearer '+KEY,'Accept':'application/json','Content-Type':'application/json'}
    if method.upper() in ('POST','PATCH','PUT'): h['Prefer']='return=representation'
    r=Request(u,data=json.dumps(body).encode() if body is not None else None,headers=h,method=method.upper())
    try:
        with urlopen(r,timeout=15) as x:
            raw=x.read().decode();return json.loads(raw) if raw else []
    except HTTPError as e: raise RuntimeError(e.read().decode(errors='replace'))
    except URLError as e: raise RuntimeError(str(e))

def getp(uid):
    rows=sb('GET','/rest/v1/worcoin_profiles',query='select=*&telegram_id=eq.%d&limit=1'%int(uid));return rows[0] if rows else None

def ensure(user):
    uid=int(user['id']);p=getp(uid)
    if p:return p
    body={'telegram_id':uid,'username':user.get('username') or '','firstname':user.get('first_name') or '','balance':0,'mininglevel':1,'boost_level':1,'refer_count':0,'energy':1000,'energy_level':1}
    rows=sb('POST','/rest/v1/worcoin_profiles',body);return rows[0] if rows else body

def regen(p):
    cap=ENERGY.get(int(p.get('energy_level') or 1),1000);rate=REGEN.get(int(p.get('energy_level') or 1),1)
    now=time.time();last=p.get('last_regen_at');
    try:last_ts=time.mktime(time.strptime(last[:19],'%Y-%m-%dT%H:%M:%S')) if last else now
    except:last_ts=now
    add=max(0,int(now-last_ts)*rate);energy=min(cap,int(p.get('energy') or 0)+add)
    return energy,now

def pub(p):
    lvl=int(p.get('boost_level') or 1);el=int(p.get('energy_level') or 1);ml=int(p.get('mininglevel') or 1);energy,now=regen(p)
    if energy!=int(p.get('energy') or 0):
        try: sb('PATCH','/rest/v1/worcoin_profiles',{'energy':energy,'last_regen_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(now))},'telegram_id=eq.%d'%int(p['telegram_id']))
        except Exception: pass
    bal=int(p.get('balance') or 0);league='Bronze' if bal<10000 else 'Silver' if bal<100000 else 'Gold' if bal<1000000 else 'Diamond'
    target=10000 if bal<10000 else 100000 if bal<100000 else 1000000 if bal<1000000 else 10000000
    return {'telegram_id':int(p.get('telegram_id') or 0),'username':p.get('username') or '','firstname':p.get('firstname') or '','balance':bal,'boost_level':lvl,'energy_level':el,'mininglevel':ml,'energy':energy,'energy_capacity':ENERGY.get(el,1000),'tap_reward':TAP.get(lvl,lvl),'regen_rate':REGEN.get(el,el),'regen_level':el,'refer_count':int(p.get('refer_count') or 0),'league':league,'next_league_target':target,'next_boost_cost':BOOST_COST.get(lvl),'next_energy_cost':ENERGY_COST.get(el),'next_regen_cost':REGEN_COST.get(el),'referral_reward':REFERRAL_REWARD,'daily_reward':DAILY_REWARD,'channel_reward':CHANNEL_REWARD,'skin':p.get('skin') or 'Default','auto_level':int(p.get('auto_level') or 0),'auto_rate':int(p.get('auto_rate') or 0)}

def patch(uid,data):
    rows=sb('PATCH','/rest/v1/worcoin_profiles',data,'telegram_id=eq.%d'%int(uid));return rows[0] if rows else getp(uid)

def current(uid):return pub(getp(uid))

def tap(uid,count):
    p=getp(uid)
    if not p:return {'success':False,'message':'Profile not found.'}
    count=max(1,min(int(count or 1),MAX_TAP));energy,now=regen(p);reward=TAP.get(int(p.get('boost_level') or 1),1);need=reward*count
    if energy<need:return {'success':False,'message':'Not enough energy.','profile':pub(p)}
    q=patch(uid,{'balance':int(p.get('balance') or 0)+need,'energy':energy-need,'last_regen_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(now))})
    return {'success':True,'profile':pub(q)}

def daily(uid):
    p=getp(uid)
    if not p:return {'success':False,'message':'Profile not found.'}
    last=p.get('last_daily_at','');today=time.strftime('%Y-%m-%d',time.gmtime())
    if last and str(last)[:10]==today:return {'success':False,'message':'Daily reward already claimed.','profile':pub(p)}
    q=patch(uid,{'balance':int(p.get('balance') or 0)+DAILY_REWARD,'last_daily_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())});return {'success':True,'profile':pub(q)}

def upgrade(uid,t):
    p=getp(uid);bal=int(p.get('balance') or 0)
    field={'boost':'boost_level','energy':'energy_level','regen':'energy_level'}.get(t)
    costs={'boost':BOOST_COST,'energy':ENERGY_COST,'regen':REGEN_COST}.get(t)
    if not field:return {'success':False,'message':'Invalid upgrade.'}
    lvl=int(p.get(field) or 1);cost=costs.get(lvl)
    if cost is None:return {'success':False,'message':'MAX LEVEL','profile':pub(p)}
    if bal<cost:return {'success':False,'message':f'Need {cost:,} WOR.','profile':pub(p)}
    q=patch(uid,{'balance':bal-cost,field:lvl+1,'energy':ENERGY.get(lvl+1,ENERGY.get(lvl,1000)) if field=='energy_level' else p.get('energy')});return {'success':True,'profile':pub(q)}

def telegram_call(method,payload):
    r=Request(f'https://api.telegram.org/bot{TOKEN}/{method}',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'},method='POST')
    with urlopen(r,timeout=12) as x:
        z=json.loads(x.read().decode());
        if not z.get('ok'):raise RuntimeError(z.get('description','Telegram API error'))
        return z.get('result')

def member(uid):
    try:
        x=telegram_call('getChatMember',{'chat_id':CHANNEL,'user_id':int(uid)});return x.get('status') in ('member','administrator','creator') or (x.get('status')=='restricted' and x.get('is_member'))
    except Exception:log.exception('channel check');return False

def init_user(s):
    if not s or not TOKEN:return None
    try:
        q=dict(parse_qsl(s,keep_blank_values=True));got=q.pop('hash',None);auth=int(q.get('auth_date','0'))
        if not got or not auth or abs(int(time.time())-auth)>86400:return None
        chk='\n'.join(f'{k}={q[k]}' for k in sorted(q));sk=hmac.new(b'WebAppData',TOKEN.encode(),hashlib.sha256).digest();calc=hmac.new(sk,chk.encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc,got):return None
        u=json.loads(q.get('user','{}'));return u if u.get('id') else None
    except:return None

class Handler(BaseHTTPRequestHandler):
    def log_message(self,fmt,*args):log.info('%s - %s',self.address_string(),fmt%args)
    def out(self,c,x):
        b=json.dumps(x,ensure_ascii=False).encode();self.send_response(c);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(b)));self.send_header('Cache-Control','no-store');self.send_header('Access-Control-Allow-Origin','*');self.end_headers();self.wfile.write(b)
    def user(self):return init_user(self.headers.get('X-Telegram-Init-Data',''))
    def body(self):
        try:return json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))).decode())
        except:return {}
    def do_OPTIONS(self):self.send_response(204);self.send_header('Access-Control-Allow-Origin','*');self.send_header('Access-Control-Allow-Headers','Content-Type,X-Telegram-Init-Data');self.end_headers()
    def do_GET(self):
        path=self.path.split('?')[0]
        if path in ('/','/index.html'):
            try:
                b=open(os.path.join(os.path.dirname(__file__),'index.html'),'rb').read();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
            except Exception as e:self.out(500,{'success':False,'error':str(e)})
            return
        u=self.user()
        try:
            if path=='/api/profile':
                if not u:return self.out(401,{'success':False,'error':'Open from Telegram.'})
                return self.out(200,{'success':True,'profile':current(u['id'])})
            if path=='/api/tasks':
                rows=sb('GET','/rest/v1/tasks',query='select=id,title,description,reward,task_url,is_active&is_active=eq.true&order=id');return self.out(200,{'success':True,'tasks':rows})
            if path=='/api/referrals':
                if not u:return self.out(401,{'success':False})
                return self.out(200,{'success':True,'referrals':[]})
            if path=='/api/rank':
                rows=sb('GET','/rest/v1/worcoin_profiles',query='select=username,firstname,balance,mininglevel&order=balance.desc&limit=100');return self.out(200,{'success':True,'ranking':[{'rank':i+1,'username':r.get('username') or '','firstname':r.get('firstname') or '','balance':int(r.get('balance') or 0),'level':int(r.get('mininglevel') or 1)} for i,r in enumerate(rows or [])]})
            return self.out(404,{'success':False,'error':'Not found'})
        except Exception as e:log.exception('GET');return self.out(500,{'success':False,'error':str(e)})
    def do_POST(self):
        u=self.user()
        if not u:return self.out(401,{'success':False,'error':'Invalid Telegram session.'})
        path=self.path.split('?')[0];b=self.body();uid=int(u['id'])
        try:
            if path=='/api/profile':return self.out(200,{'success':True,'profile':current(uid)})
            if path=='/api/tap':return self.out(200,tap(uid,b.get('count',1)))
            if path=='/api/daily':return self.out(200,daily(uid))
            if path=='/api/upgrade':return self.out(200,upgrade(uid,str(b.get('type',''))))
            if path=='/api/channel-claim':
                if not member(uid):return self.out(200,{'success':False,'message':'Join the channel first.'})
                p=getp(uid)
                if p.get('channel_claimed'):return self.out(200,{'success':False,'message':'Channel reward already claimed.','profile':pub(p)})
                q=patch(uid,{'balance':int(p.get('balance') or 0)+CHANNEL_REWARD,'channel_claimed':True});return self.out(200,{'success':True,'profile':pub(q)})
            if path=='/api/boost':return self.out(200,{'success':False,'message':'This boost will be enabled with the new database fields.'})
            if path=='/api/skin':return self.out(200,{'success':False,'message':'Skins will be enabled with the new database fields.'})
            if path=='/api/task':return self.out(200,{'success':False,'message':'Task claim requires task-claim database setup.'})
            return self.out(404,{'success':False,'error':'Not found'})
        except Exception as e:log.exception('POST');return self.out(500,{'success':False,'error':str(e)})

def serve():ThreadingHTTPServer(('0.0.0.0',PORT),Handler).serve_forever()
async def start(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    if ctx.args and ctx.args[0].startswith('ref_'):
        try:
            ref=int(ctx.args[0][4:]);uid=update.effective_user.id
            if ref!=uid:
                p=getp(ref)
                if p:patch(ref,{'balance':int(p.get('balance') or 0)+REFERRAL_REWARD,'refer_count':int(p.get('refer_count') or 0)+1})
        except Exception:log.exception('referral')
    kb=[[InlineKeyboardButton('▶ PLAY WORCOIN',web_app=WebAppInfo(url=GAME_URL))]]
    await update.message.reply_text('WORCOIN Mining\n\nTap, earn, invite friends and climb the leaderboard.',reply_markup=InlineKeyboardMarkup(kb))
async def balance(update:Update,ctx:ContextTypes.DEFAULT_TYPE):
    try:
        p=pub(getp(update.effective_user.id));await update.message.reply_text(f"💰 Balance: {p['balance']:,} WOR\n⚡ Tap: +{p['tap_reward']} WOR\n🔋 Energy: {p['energy']:,}/{p['energy_capacity']:,}")
    except Exception:await update.message.reply_text('Open WORCOIN first.')
def main():
    if not TOKEN or not SUPA or not KEY:raise RuntimeError('Missing environment variables')
    Thread(target=serve,daemon=True).start();app=Application.builder().token(TOKEN).build();app.add_handler(CommandHandler('start',start));app.add_handler(CommandHandler('balance',balance))
    try:telegram_call('setChatMenuButton',{'menu_button':{'type':'web_app','text':'▶ PLAY WORCOIN','web_app':{'url':GAME_URL}}})
    except Exception:log.exception('menu')
    log.info('WORCOIN running: %s',GAME_URL);app.run_polling(drop_pending_updates=True)
if __name__=='__main__':main()
