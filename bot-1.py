import logging
import sqlite3
import time
import json
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, PreCheckoutQueryHandler, filters
)

# ============================================================
# AYARLAR
# ============================================================
BOT_TOKEN  = "8756322634:AAEte-rPOT7WYjkKr208opGm2hvWZLRXzJY"
ADMIN_ID   = 7625144630

# Tap
COINS_PER_TAP   = 1
MAX_ENERGY      = 500
ENERGY_REGEN    = 1
TAP_COST        = 1

# Reklam
COINS_PER_AD    = 100
AD_COOLDOWN     = 30
AD_URL          = "https://senin-reklam-siten.com"

# Yıldız
STAR_TO_COIN    = 100   # 1 yıldız = 100 coin
OWNER_CUT       = 0.10  # %10 kâr

# USDT
COIN_TO_USDT    = 100000
MIN_WITHDRAW    = 10.0
USDT_WALLET     = "BURAYA_TRC20_CÜZDAN"

# Davet
REF_BONUS       = 500

# Çark ödülleri
WHEEL_PRIZES = [
    {"name": "🪙 500 Coin",       "type": "coin",    "value": 500,  "weight": 30},
    {"name": "🪙 1000 Coin",      "type": "coin",    "value": 1000, "weight": 20},
    {"name": "🪙 2000 Coin",      "type": "coin",    "value": 2000, "weight": 15},
    {"name": "🪙 5000 Coin",      "type": "coin",    "value": 5000, "weight": 8},
    {"name": "⚡ Enerji Dolumu",   "type": "energy",  "value": 0,    "weight": 15},
    {"name": "🔧 Tap Gücü +1",    "type": "upgrade", "value": "tap_power", "weight": 7},
    {"name": "🔋 Enerji Max +100","type": "upgrade", "value": "energy_max","weight": 5},
]

UPGRADES = {
    "tap_power":  {"name": "⚡ Tıklama Gücü",      "base": 500,  "mult": 2.0, "max": 10},
    "energy_max": {"name": "🔋 Enerji Kapasitesi", "base": 1000, "mult": 2.2, "max": 10},
    "energy_reg": {"name": "⚡ Enerji Yenileme",   "base": 800,  "mult": 2.0, "max": 5},
    "passive":    {"name": "🏭 Pasif Gelir",        "base": 2000, "mult": 2.5, "max": 10},
}

DAILY_TASKS = [
    {"id": "tap100",  "name": "👆 100 kez tıkla",  "target": 100, "reward": 200,  "type": "tap"},
    {"id": "tap500",  "name": "👆 500 kez tıkla",  "target": 500, "reward": 800,  "type": "tap"},
    {"id": "ad1",     "name": "📺 1 reklam izle",  "target": 1,   "reward": 150,  "type": "ad"},
    {"id": "ad3",     "name": "📺 3 reklam izle",  "target": 3,   "reward": 400,  "type": "ad"},
    {"id": "spin1",   "name": "🎰 1 çark çevir",   "target": 1,   "reward": 300,  "type": "spin"},
]

logging.basicConfig(level=logging.INFO)

# ============================================================
# VERİTABANI
# ============================================================
def get_conn():
    conn = sqlite3.connect("jebcoin.db")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT DEFAULT '',
        full_name TEXT DEFAULT '',
        coins INTEGER DEFAULT 0,
        total_coins INTEGER DEFAULT 0,
        energy INTEGER DEFAULT 500,
        last_energy REAL DEFAULT 0,
        last_ad REAL DEFAULT 0,
        last_spin TEXT DEFAULT '',
        daily_taps INTEGER DEFAULT 0,
        daily_ads INTEGER DEFAULT 0,
        daily_spins INTEGER DEFAULT 0,
        daily_date TEXT DEFAULT '',
        upgrades TEXT DEFAULT '{}',
        referrals INTEGER DEFAULT 0,
        referred_by INTEGER DEFAULT 0,
        joined TEXT DEFAULT ''
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS withdrawals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        coins INTEGER,
        usdt REAL,
        wallet TEXT,
        status TEXT DEFAULT 'pending',
        created TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS tasks_done (
        user_id INTEGER,
        task_id TEXT,
        date TEXT,
        PRIMARY KEY(user_id, task_id, date)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS star_purchases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        stars INTEGER,
        coins INTEGER,
        created TEXT
    )""")
    conn.commit()
    conn.close()

def get_user(uid):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id=?", (uid,))
    row = c.fetchone()
    conn.close()
    return row

def reg_user(uid, uname, name, ref=0):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""INSERT OR IGNORE INTO users
        (user_id, username, full_name, energy, last_energy, joined)
        VALUES (?,?,?,?,?,?)""",
        (uid, uname or "", name, MAX_ENERGY, time.time(),
         datetime.now().strftime("%Y-%m-%d %H:%M")))
    if ref:
        c.execute("UPDATE users SET referred_by=? WHERE user_id=? AND referred_by=0",
                  (ref, uid))
    conn.commit()
    conn.close()

def get_upgrades(uid):
    row = get_user(uid)
    if not row:
        return {}
    try:
        return json.loads(row["upgrades"])
    except:
        return {}

def get_stats(uid):
    upg = get_upgrades(uid)
    return {
        "cpt":  COINS_PER_TAP + upg.get("tap_power", 0),
        "me":   MAX_ENERGY + upg.get("energy_max", 0) * 100,
        "er":   ENERGY_REGEN + upg.get("energy_reg", 0),
        "pi":   upg.get("passive", 0) * 10,
    }

def calc_energy(uid):
    row = get_user(uid)
    s = get_stats(uid)
    elapsed = time.time() - row["last_energy"]
    return min(row["energy"] + int(elapsed * s["er"]), s["me"])

def set_energy(uid, e):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE users SET energy=?, last_energy=? WHERE user_id=?",
              (e, time.time(), uid))
    conn.commit()
    conn.close()

def add_coins(uid, amount):
    conn = get_conn()
    c = conn.cursor()
    if amount > 0:
        c.execute("UPDATE users SET coins=coins+?, total_coins=total_coins+? WHERE user_id=?",
                  (amount, amount, uid))
    else:
        c.execute("UPDATE users SET coins=coins+? WHERE user_id=?", (amount, uid))
    conn.commit()
    conn.close()

def reset_daily(uid):
    row = get_user(uid)
    today = datetime.now().strftime("%Y-%m-%d")
    if row["daily_date"] != today:
        conn = get_conn()
        c = conn.cursor()
        c.execute("""UPDATE users SET daily_taps=0, daily_ads=0, daily_spins=0,
                  daily_date=? WHERE user_id=?""", (today, uid))
        conn.commit()
        conn.close()

def inc_field(uid, field, amount=1):
    conn = get_conn()
    c = conn.cursor()
    c.execute(f"UPDATE users SET {field}={field}+? WHERE user_id=?", (amount, uid))
    conn.commit()
    conn.close()

def set_upgrade(uid, key, level):
    upg = get_upgrades(uid)
    upg[key] = level
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE users SET upgrades=? WHERE user_id=?",
              (json.dumps(upg), uid))
    conn.commit()
    conn.close()

def upg_cost(key, level):
    u = UPGRADES[key]
    return int(u["base"] * (u["mult"] ** level))

def task_done(uid, tid):
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT 1 FROM tasks_done WHERE user_id=? AND task_id=? AND date=?",
              (uid, tid, today))
    r = c.fetchone()
    conn.close()
    return r is not None

def mark_task(uid, tid):
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO tasks_done VALUES(?,?,?)", (uid, tid, today))
    conn.commit()
    conn.close()

def new_withdrawal(uid, coins, usdt, wallet):
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT INTO withdrawals (user_id,coins,usdt,wallet,created) VALUES(?,?,?,?,?)",
              (uid, coins, usdt, wallet, datetime.now().strftime("%Y-%m-%d %H:%M")))
    wid = c.lastrowid
    conn.commit()
    conn.close()
    return wid

def approve_w(wid):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE withdrawals SET status='approved' WHERE id=?", (wid,))
    conn.commit()
    conn.close()

def reject_w(wid, uid, coins):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE withdrawals SET status='rejected' WHERE id=?", (wid,))
    c.execute("UPDATE users SET coins=coins+? WHERE user_id=?", (coins, uid))
    conn.commit()
    conn.close()

def all_users():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    rows = [r[0] for r in c.fetchall()]
    conn.close()
    return rows

def spin_wheel():
    import random
    total = sum(p["weight"] for p in WHEEL_PRIZES)
    r = random.randint(1, total)
    cumulative = 0
    for prize in WHEEL_PRIZES:
        cumulative += prize["weight"]
        if r <= cumulative:
            return prize
    return WHEEL_PRIZES[0]

# ============================================================
# MENÜ
# ============================================================
def main_menu(uid=None):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👆 TAP ET!", callback_data="tap")],
        [InlineKeyboardButton("🎰 Çark Çevir", callback_data="spin"),
         InlineKeyboardButton("📺 Reklam İzle", callback_data="ad")],
        [InlineKeyboardButton("💰 Bakiye", callback_data="balance"),
         InlineKeyboardButton("⭐ Yıldızla Al", callback_data="buy_stars")],
        [InlineKeyboardButton("🔧 Yükseltmeler", callback_data="upgrades"),
         InlineKeyboardButton("📋 Görevler", callback_data="tasks")],
        [InlineKeyboardButton("💸 Para Çek", callback_data="withdraw"),
         InlineKeyboardButton("👥 Davet", callback_data="ref")],
        [InlineKeyboardButton("🏆 Liderlik", callback_data="top")]
    ])

def back_btn():
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Ana Menü", callback_data="back")]])

# ============================================================
# /start
# ============================================================
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    ref = 0
    if ctx.args and ctx.args[0].startswith("ref_"):
        try:
            ref = int(ctx.args[0][4:])
            if ref == u.id:
                ref = 0
        except:
            pass

    new = not get_user(u.id)
    reg_user(u.id, u.username, u.full_name, ref)

    if new and ref and get_user(ref):
        add_coins(ref, REF_BONUS)
        inc_field(ref, "referrals")
        try:
            await ctx.bot.send_message(ref,
                f"🎉 Davet ettiğin biri katıldı!\n*+{REF_BONUS} JebCoin* kazandın! 🪙",
                parse_mode="Markdown")
        except:
            pass

    row = get_user(u.id)
    s = get_stats(u.id)
    e = calc_energy(u.id)

    await update.message.reply_text(
        f"🪙 *JebCoin'e Hoş Geldin, {u.first_name}!*\n\n"
        f"Tıkla, kazan, büyü!\n\n"
        f"⚡ Enerji: *{e}/{s['me']}*\n"
        f"💎 Coin: *{row['coins']:,}*\n"
        f"👆 Güç: *{s['cpt']}x*\n\n"
        f"👇 Oynamaya başla!",
        parse_mode="Markdown",
        reply_markup=main_menu()
    )

# ============================================================
# BUTON HANDLER
# ============================================================
async def button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    u = update.effective_user
    d = q.data

    if not get_user(u.id):
        reg_user(u.id, u.username, u.full_name)
    reset_daily(u.id)

    # ── TAP ──
    if d == "tap":
        e = calc_energy(u.id)
        s = get_stats(u.id)

        if e < TAP_COST:
            await q.edit_message_text(
                f"😴 *Enerji bitti!*\n\n"
                f"⚡ {e}/{s['me']}\n\n"
                f"📺 Reklam izle → Enerji dol!\n"
                f"🎰 Çark çevir → Enerji kazan!",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📺 Reklam İzle", callback_data="ad"),
                     InlineKeyboardButton("🎰 Çark", callback_data="spin")],
                    [InlineKeyboardButton("◀️ Geri", callback_data="back")]
                ])
            )
            return

        earned = s["cpt"]
        set_energy(u.id, e - TAP_COST)
        add_coins(u.id, earned)
        inc_field(u.id, "daily_taps")

        row = get_user(u.id)
        task_msg = ""
        for t in DAILY_TASKS:
            if t["type"] == "tap" and not task_done(u.id, t["id"]):
                if row["daily_taps"] >= t["target"]:
                    mark_task(u.id, t["id"])
                    add_coins(u.id, t["reward"])
                    task_msg = f"\n\n🎯 *Görev tamamlandı! +{t['reward']} coin*"

        row = get_user(u.id)
        ne = e - TAP_COST
        bar = "🟩" * int((ne / s["me"]) * 10) + "⬜" * (10 - int((ne / s["me"]) * 10))

        await q.edit_message_text(
            f"👆 *+{earned} JebCoin!*\n\n"
            f"💎 *{row['coins']:,} coin*\n"
            f"⚡ {bar} {ne}/{s['me']}"
            f"{task_msg}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👆 TAP ET!", callback_data="tap")],
                [InlineKeyboardButton("📺 Reklam", callback_data="ad"),
                 InlineKeyboardButton("🎰 Çark", callback_data="spin")],
                [InlineKeyboardButton("◀️ Ana Menü", callback_data="back")]
            ])
        )

    # ── ÇARK ──
    elif d == "spin":
        row = get_user(u.id)
        today = datetime.now().strftime("%Y-%m-%d")
        free_used = row["last_spin"] == today

        if not free_used:
            # Bedava çark
            await q.edit_message_text(
                f"🎰 *Çark Çevirme*\n\n"
                f"🎁 Bugünlük *1 bedava* hakkın var!\n\n"
                f"Çarkı çevirmek ister misin?",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎰 Bedava Çevir!", callback_data="spin_free")],
                    [InlineKeyboardButton("◀️ Geri", callback_data="back")]
                ])
            )
        else:
            # Reklamlı çark
            elapsed = time.time() - row["last_ad"]
            can_ad = elapsed >= AD_COOLDOWN
            await q.edit_message_text(
                f"🎰 *Çark Çevirme*\n\n"
                f"✅ Bedava hakkını bugün kullandın!\n\n"
                f"📺 Reklam izleyerek tekrar çevirebilirsin!",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📺 Reklam İzle & Çevir", callback_data="spin_ad")],
                    [InlineKeyboardButton("◀️ Geri", callback_data="back")]
                ])
            )

    elif d == "spin_free":
        row = get_user(u.id)
        today = datetime.now().strftime("%Y-%m-%d")

        if row["last_spin"] == today:
            await q.answer("❌ Bugünkü bedava hakkını kullandın!", show_alert=True)
            return

        prize = spin_wheel()
        await _give_prize(u.id, prize, ctx)

        conn = get_conn()
        c = conn.cursor()
        c.execute("UPDATE users SET last_spin=? WHERE user_id=?", (today, u.id))
        conn.commit()
        conn.close()

        inc_field(u.id, "daily_spins")

        # Görev kontrolü
        row2 = get_user(u.id)
        for t in DAILY_TASKS:
            if t["type"] == "spin" and not task_done(u.id, t["id"]):
                if row2["daily_spins"] >= t["target"]:
                    mark_task(u.id, t["id"])
                    add_coins(u.id, t["reward"])

        row2 = get_user(u.id)
        await q.edit_message_text(
            f"🎰 *Çark Döndü!*\n\n"
            f"🎁 Kazandın: *{prize['name']}*\n\n"
            f"💎 Toplam: *{row2['coins']:,} coin*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🎰 Tekrar (Reklamlı)", callback_data="spin_ad")],
                [InlineKeyboardButton("◀️ Ana Menü", callback_data="back")]
            ])
        )

    elif d == "spin_ad":
        row = get_user(u.id)
        elapsed = time.time() - row["last_ad"]
        remaining = AD_COOLDOWN - elapsed

        if remaining > 0:
            await q.edit_message_text(
                f"⏳ *{int(remaining)} saniye* sonra reklam izleyebilirsin!",
                parse_mode="Markdown",
                reply_markup=back_btn()
            )
            return

        await q.edit_message_text(
            f"📺 *Reklam İzle → Çark Kazan*\n\n"
            f"1️⃣ Reklamı aç\n"
            f"2️⃣ 30 saniye bekle\n"
            f"3️⃣ Geri gel ✅ bas\n\n"
            f"🎁 Ödül: Çark hakkı!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📺 Reklamı Aç", url=AD_URL)],
                [InlineKeyboardButton("✅ İzledim, Çevir!", callback_data="spin_ad_confirm")],
                [InlineKeyboardButton("◀️ Geri", callback_data="back")]
            ])
        )

    elif d == "spin_ad_confirm":
        row = get_user(u.id)
        if time.time() - row["last_ad"] < AD_COOLDOWN:
            await q.answer("⏳ Bekle!", show_alert=True)
            return

        conn = get_conn()
        c = conn.cursor()
        c.execute("UPDATE users SET last_ad=?, daily_ads=daily_ads+1 WHERE user_id=?",
                  (time.time(), u.id))
        conn.commit()
        conn.close()

        prize = spin_wheel()
        await _give_prize(u.id, prize, ctx)
        inc_field(u.id, "daily_spins")

        row2 = get_user(u.id)
        await q.edit_message_text(
            f"🎰 *Çark Döndü!*\n\n"
            f"🎁 Kazandın: *{prize['name']}*\n\n"
            f"💎 Toplam: *{row2['coins']:,} coin*",
            parse_mode="Markdown",
            reply_markup=back_btn()
        )

    # ── REKLAM ──
    elif d == "ad":
        row = get_user(u.id)
        rem = AD_COOLDOWN - (time.time() - row["last_ad"])

        if rem > 0:
            await q.edit_message_text(
                f"⏳ *{int(rem)} saniye* sonra tekrar izleyebilirsin!",
                parse_mode="Markdown",
                reply_markup=back_btn()
            )
            return

        await q.edit_message_text(
            f"📺 *Reklam İzle*\n\n"
            f"1️⃣ Reklamı aç\n"
            f"2️⃣ 30 saniye bekle\n"
            f"3️⃣ Geri gel ✅ bas\n\n"
            f"🎁 *+{COINS_PER_AD} coin + Enerji dolumu*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📺 Reklamı Aç", url=AD_URL)],
                [InlineKeyboardButton("✅ İzledim!", callback_data="ad_confirm")],
                [InlineKeyboardButton("◀️ Geri", callback_data="back")]
            ])
        )

    elif d == "ad_confirm":
        row = get_user(u.id)
        if time.time() - row["last_ad"] < AD_COOLDOWN:
            await q.answer("⏳ Bekle!", show_alert=True)
            return

        s = get_stats(u.id)
        add_coins(u.id, COINS_PER_AD)
        set_energy(u.id, s["me"])

        conn = get_conn()
        c = conn.cursor()
        c.execute("UPDATE users SET last_ad=?, daily_ads=daily_ads+1 WHERE user_id=?",
                  (time.time(), u.id))
        conn.commit()
        conn.close()

        # Reklam görev kontrolü
        row2 = get_user(u.id)
        for t in DAILY_TASKS:
            if t["type"] == "ad" and not task_done(u.id, t["id"]):
                if row2["daily_ads"] >= t["target"]:
                    mark_task(u.id, t["id"])
                    add_coins(u.id, t["reward"])

        row2 = get_user(u.id)
        await q.edit_message_text(
            f"✅ *Reklam ödülü!*\n\n"
            f"🪙 *+{COINS_PER_AD} coin*\n"
            f"⚡ *Enerji doldu!*\n\n"
            f"💎 Toplam: *{row2['coins']:,} coin*",
            parse_mode="Markdown",
            reply_markup=main_menu()
        )

    # ── BAKİYE ──
    elif d == "balance":
        row = get_user(u.id)
        s = get_stats(u.id)
        e = calc_energy(u.id)
        usdt = row["coins"] / COIN_TO_USDT

        await q.edit_message_text(
            f"💰 *Bakiye*\n\n"
            f"💎 Coin: *{row['coins']:,}*\n"
            f"💵 USDT değeri: *{usdt:.4f}*\n"
            f"📊 Toplam kazanılan: *{row['total_coins']:,}*\n\n"
            f"⚡ Enerji: *{e}/{s['me']}*\n"
            f"👆 Tıklama gücü: *{s['cpt']}x*\n"
            f"🏭 Pasif gelir: *{s['pi']}/saat*\n\n"
            f"{'✅ Para çekebilirsin!' if usdt >= MIN_WITHDRAW else f'❌ Min {MIN_WITHDRAW} USDT gerekli'}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💸 Para Çek", callback_data="withdraw")],
                [InlineKeyboardButton("◀️ Geri", callback_data="back")]
            ])
        )

    # ── YILDIZLA AL ──
    elif d == "buy_stars":
        await q.edit_message_text(
            f"⭐ *Telegram Yıldızıyla Coin Al*\n\n"
            f"1 ⭐ = *{STAR_TO_COIN} JebCoin*\n\n"
            f"Ne kadar almak istiyorsun?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⭐ 10 Yıldız → 1,000 coin", callback_data="stars_10")],
                [InlineKeyboardButton("⭐ 50 Yıldız → 5,000 coin", callback_data="stars_50")],
                [InlineKeyboardButton("⭐ 100 Yıldız → 10,000 coin", callback_data="stars_100")],
                [InlineKeyboardButton("⭐ 500 Yıldız → 50,000 coin", callback_data="stars_500")],
                [InlineKeyboardButton("◀️ Geri", callback_data="back")]
            ])
        )

    elif d.startswith("stars_"):
        amount = int(d.split("_")[1])
        coins = amount * STAR_TO_COIN
        ctx.user_data["pending_stars"] = amount
        ctx.user_data["pending_coins"] = coins

        await ctx.bot.send_invoice(
            chat_id=u.id,
            title="JebCoin Satın Al",
            description=f"{amount} Telegram Yıldızı = {coins:,} JebCoin",
            payload=f"stars_{amount}_{u.id}",
            currency="XTR",
            prices=[LabeledPrice(f"{coins:,} JebCoin", amount)],
        )
        await q.edit_message_text(
            f"⭐ Ödeme sayfası gönderildi!\n\n"
            f"Yukarıdaki mesajdan ödemeyi tamamla.",
            reply_markup=back_btn()
        )

    # ── YÜKSELTMELEr ──
    elif d == "upgrades":
        row = get_user(u.id)
        coins = row["coins"]
        upg = get_upgrades(u.id)
        text = f"🔧 *Yükseltmeler*\n💎 *{coins:,} coin*\n\n"
        buttons = []

        for key, val in UPGRADES.items():
            lv = upg.get(key, 0)
            if lv >= val["max"]:
                buttons.append([InlineKeyboardButton(f"{val['name']} ✅ MAX", callback_data="maxed")])
            else:
                cost = upg_cost(key, lv)
                ok = "✅" if coins >= cost else "❌"
                buttons.append([InlineKeyboardButton(
                    f"{val['name']} Lv{lv+1} — {cost:,} {ok}",
                    callback_data=f"buy_{key}"
                )])

        buttons.append([InlineKeyboardButton("◀️ Geri", callback_data="back")])
        await q.edit_message_text(text, parse_mode="Markdown",
                                  reply_markup=InlineKeyboardMarkup(buttons))

    elif d.startswith("buy_") and d != "buy_stars":
        key = d[4:]
        if key not in UPGRADES:
            return
        row = get_user(u.id)
        upg = get_upgrades(u.id)
        lv = upg.get(key, 0)

        if lv >= UPGRADES[key]["max"]:
            await q.answer("MAX seviye!", show_alert=True)
            return

        cost = upg_cost(key, lv)
        if row["coins"] < cost:
            await q.answer(f"❌ {cost:,} coin gerekli!", show_alert=True)
            return

        add_coins(u.id, -cost)
        set_upgrade(u.id, key, lv + 1)
        await q.answer(f"✅ {UPGRADES[key]['name']} Lv{lv+1} alındı!", show_alert=True)

        # Menüyü yenile
        row = get_user(u.id)
        coins = row["coins"]
        upg = get_upgrades(u.id)
        buttons = []
        for k, v in UPGRADES.items():
            l = upg.get(k, 0)
            if l >= v["max"]:
                buttons.append([InlineKeyboardButton(f"{v['name']} ✅ MAX", callback_data="maxed")])
            else:
                c2 = upg_cost(k, l)
                ok = "✅" if coins >= c2 else "❌"
                buttons.append([InlineKeyboardButton(
                    f"{v['name']} Lv{l+1} — {c2:,} {ok}",
                    callback_data=f"buy_{k}"
                )])
        buttons.append([InlineKeyboardButton("◀️ Geri", callback_data="back")])
        await q.edit_message_text(
            f"🔧 *Yükseltmeler*\n💎 *{coins:,} coin*\n\n",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif d == "maxed":
        await q.answer("🏆 Maksimum seviye!", show_alert=True)

    # ── GÖREVLER ──
    elif d == "tasks":
        today = datetime.now().strftime("%Y-%m-%d")
        text = f"📋 *Günlük Görevler*\n_{today}_\n\n"
        for t in DAILY_TASKS:
            done = task_done(u.id, t["id"])
            text += f"{'✅' if done else '🔲'} {t['name']} → *+{t['reward']} coin*\n"
        text += "\n💡 Her gece sıfırlanır!"
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=back_btn())

    # ── PARA ÇEK ──
    elif d == "withdraw":
        row = get_user(u.id)
        coins = row["coins"]
        usdt = coins / COIN_TO_USDT

        if usdt < MIN_WITHDRAW:
            needed = int(MIN_WITHDRAW * COIN_TO_USDT) - coins
            await q.edit_message_text(
                f"❌ *Yetersiz Bakiye*\n\n"
                f"Min: *{MIN_WITHDRAW} USDT*\n"
                f"Mevcut: *{usdt:.4f} USDT*\n"
                f"Eksik: *{needed:,} coin*",
                parse_mode="Markdown",
                reply_markup=back_btn()
            )
            return

        ctx.user_data["withdraw"] = True
        ctx.user_data["w_coins"] = coins
        ctx.user_data["w_usdt"] = usdt

        await q.edit_message_text(
            f"💸 *Para Çekme*\n\n"
            f"💵 Çekilecek: *{usdt:.4f} USDT*\n"
            f"💎 Coin: *{coins:,}*\n\n"
            f"📝 TRC-20 cüzdan adresini yaz:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ İptal", callback_data="back")]])
        )

    # ── DAVET ──
    elif d == "ref":
        me = await ctx.bot.get_me()
        link = f"https://t.me/{me.username}?start=ref_{u.id}"
        row = get_user(u.id)
        await q.edit_message_text(
            f"👥 *Davet Sistemi*\n\n"
            f"Her davet = *{REF_BONUS} coin* 🎁\n"
            f"Toplam davetlerin: *{row['referrals']}*\n"
            f"Toplam kazanç: *{row['referrals'] * REF_BONUS:,} coin*\n\n"
            f"🔗 Davet linkin:\n`{link}`",
            parse_mode="Markdown",
            reply_markup=back_btn()
        )

    # ── LİDERLİK ──
    elif d == "top":
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT full_name, total_coins FROM users ORDER BY total_coins DESC LIMIT 10")
        rows = c.fetchall()
        conn.close()

        medals = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
        text = "🏆 *Top 10 JebCoin*\n\n"
        for i, row in enumerate(rows):
            text += f"{medals[i]} {row['full_name']}: *{row['total_coins']:,}*\n"

        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=back_btn())

    # ── GERİ ──
    elif d == "back":
        row = get_user(u.id)
        s = get_stats(u.id)
        e = calc_energy(u.id)
        await q.edit_message_text(
            f"🪙 *JebCoin*\n\n"
            f"💎 *{row['coins']:,} coin* | ⚡ *{e}/{s['me']}*",
            parse_mode="Markdown",
            reply_markup=main_menu()
        )

    # ── ADMİN ONAY ──
    elif d.startswith("appw_"):
        if u.id != ADMIN_ID:
            return
        approve_w(int(d[5:]))
        await q.edit_message_text("✅ Çekim onaylandı.")

    elif d.startswith("rejw_"):
        if u.id != ADMIN_ID:
            return
        parts = d.split("_")
        reject_w(int(parts[1]), int(parts[2]), int(parts[3]))
        await q.edit_message_text("❌ Çekim reddedildi.")
        try:
            await ctx.bot.send_message(int(parts[2]),
                "❌ Çekim talebiniz reddedildi. Coinleriniz iade edildi.")
        except:
            pass

# ============================================================
# ÖDÜL VER
# ============================================================
async def _give_prize(uid, prize, ctx):
    if prize["type"] == "coin":
        add_coins(uid, prize["value"])
    elif prize["type"] == "energy":
        s = get_stats(uid)
        set_energy(uid, s["me"])
    elif prize["type"] == "upgrade":
        key = prize["value"]
        upg = get_upgrades(uid)
        lv = upg.get(key, 0)
        if lv < UPGRADES[key]["max"]:
            set_upgrade(uid, key, lv + 1)

# ============================================================
# MESAJ HANDLER (cüzdan adresi)
# ============================================================
async def msg_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if ctx.user_data.get("withdraw"):
        wallet = update.message.text.strip()
        if not (wallet.startswith("T") and len(wallet) == 34):
            await update.message.reply_text("❌ Geçersiz TRC-20 adresi! T ile başlayan 34 karakter olmalı.")
            return

        coins = ctx.user_data["w_coins"]
        usdt = ctx.user_data["w_usdt"]
        add_coins(u.id, -coins)
        wid = new_withdrawal(u.id, coins, usdt, wallet)
        ctx.user_data["withdraw"] = False

        await update.message.reply_text(
            f"✅ *Talep alındı!*\n\n"
            f"💵 *{usdt:.4f} USDT*\n"
            f"📬 `{wallet}`\n"
            f"⏳ 24 saat içinde işleme alınır.",
            parse_mode="Markdown",
            reply_markup=main_menu()
        )

        try:
            await ctx.bot.send_message(
                ADMIN_ID,
                f"💸 *Çekim #{wid}*\n\n"
                f"👤 {u.full_name} (`{u.id}`)\n"
                f"💵 *{usdt:.4f} USDT* ({coins:,} coin)\n"
                f"📬 `{wallet}`",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Onayla", callback_data=f"appw_{wid}"),
                    InlineKeyboardButton("❌ Reddet", callback_data=f"rejw_{wid}_{u.id}_{coins}")
                ]])
            )
        except:
            pass

# ============================================================
# YILDIZ ÖDEME
# ============================================================
async def precheckout(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def successful_payment(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    payment = update.message.successful_payment
    stars = payment.total_amount
    coins = stars * STAR_TO_COIN

    add_coins(u.id, coins)

    # Admin kâr kaydı
    owner_cut = int(stars * OWNER_CUT)
    try:
        await ctx.bot.send_message(
            ADMIN_ID,
            f"⭐ *Yıldız Satışı*\n\n"
            f"👤 {u.full_name} (`{u.id}`)\n"
            f"⭐ {stars} yıldız → {coins:,} coin\n"
            f"💰 Senin kârın: ~{owner_cut} yıldız (%10)"
        )
    except:
        pass

    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT INTO star_purchases (user_id,stars,coins,created) VALUES(?,?,?,?)",
              (u.id, stars, coins, datetime.now().strftime("%Y-%m-%d %H:%M")))
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"✅ *Satın alma başarılı!*\n\n"
        f"⭐ {stars} yıldız\n"
        f"🪙 *+{coins:,} JebCoin* hesabına eklendi!",
        parse_mode="Markdown",
        reply_markup=main_menu()
    )

# ============================================================
# ADMİN KOMUTLARI
# ============================================================
async def stats_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*), SUM(total_coins) FROM users")
    r = c.fetchone()
    c.execute("SELECT COUNT(*), SUM(usdt) FROM withdrawals WHERE status='pending'")
    p = c.fetchone()
    c.execute("SELECT COUNT(*), SUM(stars) FROM star_purchases")
    s = c.fetchone()
    conn.close()

    await update.message.reply_text(
        f"📊 *JebCoin İstatistik*\n\n"
        f"👥 Kullanıcı: *{r[0]}*\n"
        f"💎 Toplam coin: *{r[1] or 0:,}*\n\n"
        f"⭐ Yıldız satışı: *{s[0]}* işlem ({s[1] or 0} ⭐)\n"
        f"⏳ Bekleyen çekim: *{p[0]}* ({p[1] or 0:.2f} USDT)",
        parse_mode="Markdown"
    )

async def broadcast_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not ctx.args:
        await update.message.reply_text("Kullanım: /broadcast Mesaj")
        return
    msg = " ".join(ctx.args)
    sent = 0
    for uid in all_users():
        try:
            await ctx.bot.send_message(uid, msg)
            sent += 1
        except:
            pass
    await update.message.reply_text(f"✅ {sent} kişiye gönderildi.")

async def addcoins_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if len(ctx.args) < 2:
        await update.message.reply_text("Kullanım: /addcoins USER_ID MİKTAR")
        return
    add_coins(int(ctx.args[0]), int(ctx.args[1]))
    await update.message.reply_text(f"✅ {ctx.args[1]} coin eklendi.")

# ============================================================
# MAIN
# ============================================================
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("addcoins", addcoins_cmd))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, msg_handler))

    print("🚀 JebCoin Bot başlatıldı!")
    app.run_polling()

if __name__ == "__main__":
    main()
