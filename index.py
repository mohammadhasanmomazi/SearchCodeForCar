import os
import logging
import sqlite3
import time
from dotenv import load_dotenv
from telebot import TeleBot, types
from flask import Flask, request
import json

# ================= CONFIG =================
load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')
OWNER_ID = int(os.getenv('OWNER_ID', '77126477'))
MODE = os.getenv('MODE', 'POLLING').upper()
WEBHOOK_URL = os.getenv('WEBHOOK_URL')
WEBHOOK_PORT = int(os.getenv('WEBHOOK_PORT', 8443))
WEBHOOK_PATH = os.getenv('WEBHOOK_PATH', '/webhook')

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is required")

# ================= LOGGING =================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= BOT & DB =================
bot = TeleBot(BOT_TOKEN)
app = Flask(__name__)
DB_NAME = "users.db"


def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    phone TEXT,
                    province TEXT,
                    city TEXT,
                    state TEXT,
                    role TEXT DEFAULT 'user',
                    subscription_end INTEGER
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS sponsor_channels (
                    id INTEGER PRIMARY KEY,
                    username TEXT UNIQUE
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS admin_link (
                    id INTEGER PRIMARY KEY,
                    link TEXT UNIQUE
                )''')
    conn.commit()
    conn.close()


init_db()

# ================= LOAD SPONSOR CHANNELS =================
sponsor_channels = []
temp_admin_action = {}
notified_channels = set()


def load_sponsor_channels():
    global sponsor_channels
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT username FROM sponsor_channels")
    rows = c.fetchall()
    conn.close()
    sponsor_channels = [row[0] for row in rows]


load_sponsor_channels()

# ================= LOAD PROVINCES & CITIES =================
with open("iran_cities.json", "r", encoding="utf-8") as f:
    provinces = json.load(f)

# ================= HELPERS =================


def days_left(timestamp):
    if not timestamp:
        return 0
    now = int(time.time())
    diff = timestamp - now
    return max(0, diff // 86400)


def is_admin(user_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT role FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] in ['owner', 'admin'] if row else False


def is_member_of_sponsor(user_id):
    if not sponsor_channels:
        return True
    for channel in sponsor_channels:
        try:
            member = bot.get_chat_member(channel, user_id)
            if member.status not in ['member', 'administrator', 'creator']:
                return False
        except Exception as e:
            logger.error(f"Error checking membership for {channel}: {e}")
            if "member list is inaccessible" in str(e) and channel not in notified_channels:
                bot.send_message(
                    OWNER_ID, f"ربات باید ادمین کانال {channel} باشد تا بتواند عضویت کاربران را بررسی کند.")
                notified_channels.add(channel)
            return False
    return True


def get_admin_link():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT link FROM admin_link LIMIT 1")
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


def edit_panel(chat_id, msg_id, text, buttons):
    markup = types.InlineKeyboardMarkup()
    for b_text, b_data in buttons:
        markup.add(types.InlineKeyboardButton(b_text, callback_data=b_data))
    bot.edit_message_text(chat_id=chat_id, message_id=msg_id,
                          text=text, reply_markup=markup)

# ================= REGISTRATION =================


@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        "SELECT state, subscription_end FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()

    if row and row[0] == 'done':
        left = days_left(row[1])

        # Main menu buttons - keyboard
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add("وضعیت اشتراک ⏳", "👤 ارتباط با ادمین")
        markup.add("📦 کالای من", "📋 لیست کالاها")

        bot.send_message(
            message.chat.id, f"برای دریافت لیست قیمت، کد کالا یا نام آن را وارد نمایید:", reply_markup=markup)
    else:
        markup = types.ReplyKeyboardMarkup(
            one_time_keyboard=True, resize_keyboard=True)
        btn = types.KeyboardButton("ارسال شماره 📱", request_contact=True)
        markup.add(btn)
        bot.send_message(
            message.chat.id, "برای ادامه ثبت نام، لطفا شماره تماس خود را ارسال کنید:", reply_markup=markup)


@bot.message_handler(content_types=['contact'])
def contact_handler(message):
    user_id = message.from_user.id
    phone = message.contact.phone_number
    role = 'owner' if user_id == OWNER_ID else 'user'

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO users (user_id, phone, state, role) VALUES (?, ?, ?, ?)",
              (user_id, phone, 'province', role))
    conn.commit()
    conn.close()

    bot.send_message(message.chat.id, "با تشکر! شماره شما دریافت شد.")
    send_province_selection(message.chat.id)


def send_province_selection(chat_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for province in provinces.keys():
        markup.add(province)
    bot.send_message(
        chat_id, "لطفاً استان خود را انتخاب کنید:", reply_markup=markup)


def send_city_selection(chat_id, province):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for city in provinces[province]:
        markup.add(city)
    bot.send_message(chat_id, "شهر خود را انتخاب کنید:", reply_markup=markup)

# ================= ADMIN PANEL =================


@bot.message_handler(commands=['panel'])
def admin_panel(message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        bot.send_message(message.chat.id, "شما دسترسی به پنل مدیریت ندارید.")
        return

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT role FROM users WHERE user_id=?", (user_id,))
    role = c.fetchone()[0]
    conn.close()

    buttons = [("لیست کاربران", "list_users"),
               ("مدیریت اشتراک‌ها", "manage_sub"),
               ("مدیریت کانال‌های اسپانسر", "manage_sponsor")]
    if role == 'owner':
        buttons.append(("مدیریت ادمین‌ها", "manage_admins"))
        buttons.append(("تنظیم لینک پشتیبانی", "set_admin_link"))
    markup = types.InlineKeyboardMarkup()
    for text, data in buttons:
        markup.add(types.InlineKeyboardButton(text, callback_data=data))
    bot.send_message(message.chat.id, "پنل مدیریت", reply_markup=markup)

# ================= CALLBACK HANDLER =================


@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    user_id = call.from_user.id
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT role FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    role = row[0] if row else None
    conn.close()

    # Handle sponsor check for all users
    if call.data == 'check_sponsor':
        if is_member_of_sponsor(call.from_user.id):
            bot.send_message(
                call.message.chat.id, "✅ شما عضو کانال‌ها شدید. اکنون می‌توانید از ربات استفاده کنید.")
        else:
            bot.answer_callback_query(
                call.id, "هنوز عضو نشده‌اید.", show_alert=True)
        return

    # Handle subscription status for all users
    if call.data == 'subscription_status':
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT subscription_end FROM users WHERE user_id=?",
                  (call.from_user.id,))
        row = c.fetchone()
        conn.close()
        left = days_left(row[0]) if row and row[0] else 0
        bot.answer_callback_query(
            call.id, f"روزهای باقیمانده اشتراک: {left}", show_alert=True)
        return

    # Handle admin contact for all users
    if call.data == 'admin_contact':
        admin_link = get_admin_link()
        if admin_link:
            bot.answer_callback_query(
                call.id, f"لینک پشتیبانی: {admin_link}", show_alert=True)
        else:
            bot.answer_callback_query(
                call.id, "لینک پشتیبانی تنظیم نشده است. لطفاً با ادمین تماس بگیرید.", show_alert=True)
        return

    if role not in ['owner', 'admin']:
        bot.answer_callback_query(call.id, "شما دسترسی ندارید.")
        return

    chat_id = call.message.chat.id
    msg_id = call.message.message_id

    # --- بازگشت به پنل اصلی ---
    if call.data == 'main_panel':
        buttons = [("لیست کاربران", "list_users"),
                   ("مدیریت اشتراک‌ها", "manage_sub"),
                   ("مدیریت کانال‌های اسپانسر", "manage_sponsor")]
        if role == 'owner':
            buttons.append(("مدیریت ادمین‌ها", "manage_admins"))
            buttons.append(("تنظیم لینک پشتیبانی", "set_admin_link"))
        edit_panel(chat_id, msg_id, "پنل مدیریت", buttons)
        return

    # --- تنظیم لینک پشتیبانی ---
    if call.data == 'set_admin_link' and role == 'owner':
        temp_admin_action[user_id] = 'set_admin_link'
        bot.send_message(chat_id, "لطفاً لینک پشتیبانی جدید را ارسال کنید:")
        return

    # --- لیست کاربران ---
    elif call.data == 'list_users':
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT user_id, phone, role, subscription_end FROM users")
        users = c.fetchall()
        conn.close()
        text = "📋 لیست کاربران:\n"
        for u in users:
            left = days_left(u[3])
            text += f"ID: {u[0]}, شماره: {u[1]}, نقش: {u[2]}, روزهای باقیمانده: {left}\n"
        edit_panel(chat_id, msg_id, text, [("بازگشت به پنل", "main_panel")])

    # --- مدیریت اشتراک ---
    elif call.data == 'manage_sub':
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT user_id, phone FROM users")
        users = c.fetchall()
        conn.close()
        buttons = [(f"{u[1]} ({u[0]})", f"sub_user_{u[0]}") for u in users]
        buttons.append(("بازگشت به پنل", "main_panel"))
        edit_panel(chat_id, msg_id, "انتخاب کاربر برای افزودن اشتراک:", buttons)

    elif call.data.startswith("sub_user_"):
        target_id = int(call.data.split("_")[2])
        buttons = [("افزودن 30 روز", f"add_days_{target_id}_30"),
                   ("افزودن 60 روز", f"add_days_{target_id}_60"),
                   ("ورود دستی تعداد روز", f"manual_days_{target_id}"),
                   ("بازگشت به پنل", "main_panel")]
        edit_panel(
            chat_id, msg_id, f"انتخاب میزان افزودن اشتراک برای کاربر {target_id}:", buttons)

    elif call.data.startswith("add_days_"):
        parts = call.data.split("_")
        target_id = int(parts[2])
        days = int(parts[3])
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute(
            "SELECT subscription_end FROM users WHERE user_id=?", (target_id,))
        row = c.fetchone()
        now = int(time.time())
        current_end = row[0] if row and row[0] and row[0] > now else now
        new_end = current_end + days * 86400
        c.execute("UPDATE users SET subscription_end=? WHERE user_id=?",
                  (new_end, target_id))
        conn.commit()
        conn.close()
        edit_panel(chat_id, msg_id, f"✅ اشتراک {days} روز به کاربر {target_id} اضافه شد.", [
                   ("بازگشت به پنل", "main_panel")])
    elif call.data.startswith("manual_days_"):
        target_id = int(call.data.split("_")[2])
        temp_admin_action[call.from_user.id] = f"manual_days_{target_id}"
        bot.send_message(
            chat_id, "لطفاً تعداد روزهای اشتراک را به صورت عدد وارد کنید:")

    # --- مدیریت ادمین‌ها (فقط Owner) ---
    elif call.data == 'manage_admins' and role == 'owner':
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT user_id, phone, role FROM users")
        users = c.fetchall()
        conn.close()
        buttons = []
        for u in users:
            if u[0] != OWNER_ID:
                if u[2] == 'admin':
                    buttons.append(
                        (f"حذف ادمین {u[1]}", f"remove_admin_{u[0]}"))
                else:
                    buttons.append(
                        (f"افزودن ادمین {u[1]}", f"add_admin_{u[0]}"))
        buttons.append(("بازگشت به پنل", "main_panel"))
        edit_panel(chat_id, msg_id, "مدیریت ادمین‌ها:", buttons)

    elif call.data.startswith("add_admin_") and role == 'owner':
        target_id = int(call.data.split("_")[2])
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("UPDATE users SET role='admin' WHERE user_id=?", (target_id,))
        conn.commit()
        conn.close()
        edit_panel(chat_id, msg_id, f"✅ کاربر {target_id} اکنون ادمین شد.", [
                   ("بازگشت به پنل", "main_panel")])

    elif call.data.startswith("remove_admin_") and role == 'owner':
        target_id = int(call.data.split("_")[2])
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("UPDATE users SET role='user' WHERE user_id=?", (target_id,))
        conn.commit()
        conn.close()
        edit_panel(chat_id, msg_id, f"✅ کاربر {target_id} دیگر ادمین نیست.", [
                   ("بازگشت به پنل", "main_panel")])

    # --- مدیریت کانال‌های اسپانسر ---
    elif call.data == 'manage_sponsor':
        buttons = []
        for ch in sponsor_channels:
            buttons.append((f"حذف {ch}", f"remove_sponsor_{ch}"))
        buttons.append(("افزودن کانال جدید", "add_sponsor"))
        buttons.append(("بازگشت به پنل", "main_panel"))
        edit_panel(chat_id, msg_id, "مدیریت کانال‌های اسپانسر:", buttons)

    elif call.data.startswith("remove_sponsor_"):
        channel = call.data.split("_", 2)[2]
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("DELETE FROM sponsor_channels WHERE username=?", (channel,))
        conn.commit()
        conn.close()
        load_sponsor_channels()
        edit_panel(chat_id, msg_id, f"✅ کانال {channel} حذف شد.", [
                   ("بازگشت به پنل", "main_panel")])

    elif call.data == 'add_sponsor':
        temp_admin_action[user_id] = 'add_sponsor'
        bot.send_message(
            chat_id, "لطفاً یوزرنیم کانال جدید را ارسال کنید (مثال: @channelname):")

# ================= MESSAGE HANDLER =================


@bot.message_handler(func=lambda msg: True)
def handle_messages(message):
    # اولویت به /panel بده
    if message.text == "/panel":
        admin_panel(message)
        return

    user_id = message.from_user.id
    chat_id = message.chat.id

    if user_id in temp_admin_action:
        action = temp_admin_action[user_id]
        if action == 'add_sponsor':
            if message.text.startswith('@'):
                channel = message.text
                conn = sqlite3.connect(DB_NAME)
                c = conn.cursor()
                c.execute(
                    "INSERT OR IGNORE INTO sponsor_channels (username) VALUES (?)", (channel,))
                conn.commit()
                conn.close()
                load_sponsor_channels()
                bot.send_message(chat_id, f"✅ کانال {channel} اضافه شد.")
            else:
                bot.send_message(chat_id, "❌ یوزرنیم باید با @ شروع شود.")
            del temp_admin_action[user_id]
            return
        elif action.startswith('manual_days_'):
            target_id = int(action.split('_')[2])
            try:
                days = int(message.text)
                if days <= 0:
                    raise ValueError
                conn = sqlite3.connect(DB_NAME)
                c = conn.cursor()
                c.execute(
                    "SELECT subscription_end FROM users WHERE user_id=?", (target_id,))
                row = c.fetchone()
                now = int(time.time())
                current_end = row[0] if row and row[0] and row[0] > now else now
                new_end = current_end + days * 86400
                c.execute(
                    "UPDATE users SET subscription_end=? WHERE user_id=?", (new_end, target_id))
                conn.commit()
                conn.close()
                bot.send_message(
                    chat_id, f"✅ اشتراک {days} روز به کاربر {target_id} اضافه شد.")
            except ValueError:
                bot.send_message(chat_id, "❌ لطفاً یک عدد مثبت وارد کنید.")
            del temp_admin_action[user_id]
            return
        elif action == 'set_admin_link':
            link = message.text.strip()
            if not link.startswith("http"):
                bot.send_message(chat_id, "❌ لطفاً یک لینک معتبر ارسال کنید.")
                return
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            c.execute("DELETE FROM admin_link")
            c.execute("INSERT INTO admin_link (link) VALUES (?)", (link,))
            conn.commit()
            conn.close()
            bot.send_message(chat_id, f"✅ لینک پشتیبانی به روز شد: {link}")
            del temp_admin_action[user_id]
            return

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        "SELECT state, province, subscription_end FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()

    state = row[0] if row else None

    if state == 'province':
        if message.text in provinces.keys():
            province = message.text
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            c.execute("UPDATE users SET province=?, state=? WHERE user_id=?",
                      (province, 'city', user_id))
            conn.commit()
            conn.close()
            bot.send_message(
                message.chat.id, f"استان {province} انتخاب شد. لطفاً شهر خود را انتخاب کنید:")
            send_city_selection(message.chat.id, province)
        else:
            bot.send_message(
                message.chat.id, "❌ لطفاً یک استان معتبر از لیست انتخاب کنید.")

    elif state == 'city':
        province = row[1]
        if message.text in provinces[province]:
            city = message.text
            conn = sqlite3.connect(DB_NAME)
            c = conn.cursor()
            c.execute("UPDATE users SET city=?, state=? WHERE user_id=?",
                      (city, 'done', user_id))
            conn.commit()
            conn.close()
            bot.send_message(
                message.chat.id, f"✅ ثبت‌نام شما تکمیل شد. شهر {city} انتخاب شد.", reply_markup=types.ReplyKeyboardRemove())
            bot.send_message(
                message.chat.id, "برای دریافت لیست قیمت، کد کالا یا نام آن را وارد نمایید:")
        else:
            bot.send_message(
                message.chat.id, "❌ لطفاً فقط از لیست شهرهای نمایش داده شده انتخاب کنید.")

    elif state == 'done':
        if not is_admin(user_id) and not is_member_of_sponsor(user_id):
            markup = types.InlineKeyboardMarkup()
            for channel in sponsor_channels:
                markup.add(types.InlineKeyboardButton(
                    f"عضویت در {channel}", url=f"https://t.me/{channel[1:]}"))
            markup.add(types.InlineKeyboardButton(
                "بررسی عضویت", callback_data="check_sponsor"))
            bot.send_message(
                message.chat.id, "برای استفاده از ربات باید عضو کانال‌های اسپانسر باشید:", reply_markup=markup)
            return
        left = days_left(row[2])

        # Handle menu buttons
        if message.text == "👤 ارتباط با ادمین":
            admin_link = get_admin_link()
            if admin_link:
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton(
                    "شروع گفتگو 💭", url=admin_link))
                bot.send_message(
                    chat_id, "برای ارتباط با پشتیبانی، روی دکمه زیر کلیک کنید:", reply_markup=markup)
            else:
                bot.send_message(
                    chat_id, "لینک پشتیبانی تنظیم نشده است. لطفاً با ادمین تماس بگیرید.")
            return
        elif message.text.startswith("وضعیت اشتراک ⏳"):
            bot.send_message(chat_id, f"روزهای باقیمانده اشتراک: {left} ⏳")
            return
        elif message.text == "📋 لیست کالاها":
            bot.send_message(chat_id, "لیست کالاها در حال حاضر خالی است.")
            return
        elif message.text == "📦 کالای من":
            bot.send_message(chat_id, "شما کالایی ندارید.")
            return

        # Main menu buttons - keyboard
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add("وضعیت اشتراک ⏳", "👤 ارتباط با ادمین")
        markup.add("📦 کالای من", "📋 لیست کالاها")

        bot.send_message(
            message.chat.id, f"🔎 جستجو: {message.text}", reply_markup=markup)

    else:
        bot.send_message(
            message.chat.id, "لطفاً با /start ثبت‌نام را آغاز کنید.")

# ================= WEBHOOK =================


@app.route(WEBHOOK_PATH, methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        bot.process_new_updates(
            [bot._telebot_types.Update.de_json(json_string)])
        return 'OK', 200
    else:
        return 'Bad Request', 400

# ================= MAIN =================


def main():
    if MODE == 'WEBHOOK':
        if not WEBHOOK_URL:
            raise ValueError("WEBHOOK_URL is required for webhook mode")
        logger.info("Starting bot in WEBHOOK mode")
        bot.remove_webhook()
        bot.set_webhook(url=WEBHOOK_URL + WEBHOOK_PATH)
        app.run(host='0.0.0.0', port=WEBHOOK_PORT)
    else:
        logger.info("Starting bot in POLLING mode")
        bot.remove_webhook()
        bot.polling(non_stop=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
        raise
