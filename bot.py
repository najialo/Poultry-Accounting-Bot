# -*- coding: utf-8 -*-
"""
بوت حسابات مزرعة الدواجن
يسجل: صناديق البيض، المصاريف، الدخل - ويرد بتقارير حسب أي فترة زمنية
"""

import csv
import os
import re
import logging
import calendar
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, ContextTypes, filters

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("لازم تحط BOT_TOKEN كـ Environment Variable")

ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")

EGGS_FILE = "eggs.csv"
FINANCE_FILE = "finance.csv"

logging.basicConfig(level=logging.INFO)

# ============ الملفات ============

def ensure_files():
    if not os.path.exists(EGGS_FILE):
        with open(EGGS_FILE, "w", newline="", encoding="utf-8-sig") as f:
            csv.writer(f).writerow(["التاريخ", "الكمية"])
    if not os.path.exists(FINANCE_FILE):
        with open(FINANCE_FILE, "w", newline="", encoding="utf-8-sig") as f:
            csv.writer(f).writerow(["التاريخ", "النوع", "المبلغ", "الوصف"])


def normalize_text(text: str) -> str:
    text = text.strip()
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    text = text.replace("ة", "ه").replace("ى", "ي")
    return text


# ============ فهم التاريخ ============

MONTHS = {
    "يناير": 1, "كانون الثاني": 1,
    "فبراير": 2, "شباط": 2,
    "مارس": 3, "اذار": 3,
    "ابريل": 4, "نيسان": 4,
    "مايو": 5, "ايار": 5,
    "يونيو": 6, "حزيران": 6,
    "يوليو": 7, "تموز": 7,
    "اغسطس": 8, "اب": 8,
    "سبتمبر": 9, "ايلول": 9,
    "اكتوبر": 10, "تشرين الاول": 10,
    "نوفمبر": 11, "تشرين الثاني": 11,
    "ديسمبر": 12, "كانون الاول": 12,
}


def find_month_in_text(text: str):
    for name, num in MONTHS.items():
        if name in text:
            return num
    return None


def parse_explicit_date(text: str, ref_year: int):
    """يدور عن تاريخ صريح متل '15 اغسطس' أو '15.8' أو '15/8/2026'"""
    text = normalize_text(text)

    # صيغة يوم.شهر.سنة أو يوم/شهر/سنة رقمية بالكامل
    m = re.search(r"\b(\d{1,2})[./\-](\d{1,2})[./\-](\d{2,4})\b", text)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        try:
            return datetime(y, mo, d)
        except ValueError:
            pass

    # صيغة يوم.شهر رقمية بس (بدون سنة - نفترض السنة الحالية)
    m = re.search(r"\b(\d{1,2})[./\-](\d{1,2})\b", text)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        try:
            return datetime(ref_year, mo, d)
        except ValueError:
            pass

    # صيغة "15 اغسطس" أو "15.اغسطس" (رقم + اسم شهر عربي)
    m = re.search(r"\b(\d{1,2})\s*[.\-]?\s*(" + "|".join(MONTHS.keys()) + r")\b", text)
    if m:
        d = int(m.group(1))
        mo = MONTHS[m.group(2)]
        try:
            return datetime(ref_year, mo, d)
        except ValueError:
            pass

    return None


def month_range(year: int, month: int):
    start = datetime(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    end = datetime(year, month, last_day, 23, 59, 59)
    return start, end


def parse_period(text: str):
    """
    يرجع (start_date, end_date, label) حسب الكلام المكتوب.
    يغطي: اليوم/امبارح/الاسبوع/الشهر/الشهرين/عدة شهور/السنة/شهر باسمه/فترة محددة/الكل
    """
    norm = normalize_text(text)
    today = datetime.now()

    # اليوم
    if any(w in norm for w in ["اليوم", "النهارده", "هلق"]):
        start = datetime(today.year, today.month, today.day)
        end = start.replace(hour=23, minute=59, second=59)
        return start, end, "اليوم"

    # امبارح
    if any(w in norm for w in ["امبارح", "البارحه", "أمس"]):
        y = today - timedelta(days=1)
        start = datetime(y.year, y.month, y.day)
        end = start.replace(hour=23, minute=59, second=59)
        return start, end, "امبارح"

    # فترة محددة "من ... ل/لـ/الى/حتى ..."
    m = re.search(r"من\s+(.+?)\s+(?:ل|لـ|الى|إلى|حتى)\s+(.+)", norm)
    if m:
        d1 = parse_explicit_date(m.group(1), today.year)
        d2 = parse_explicit_date(m.group(2), today.year)
        if d1 and d2:
            d2 = d2.replace(hour=23, minute=59, second=59)
            return d1, d2, f"من {d1.strftime('%d/%m/%Y')} إلى {d2.strftime('%d/%m/%Y')}"

    # عدد شهور محدد "اخر 3 اشهر" / "3 شهور" / "تلات شهور"
    m = re.search(r"(?:اخر|آخر)?\s*(\d+)\s*(?:شهر|شهور|اشهر|أشهر)", norm)
    if m:
        n = int(m.group(1))
        end = today
        start_month_idx = today.month - n
        y = today.year
        while start_month_idx <= 0:
            start_month_idx += 12
            y -= 1
        start = datetime(y, start_month_idx, 1)
        return start, end, f"آخر {n} شهور"

    # الشهرين (بدون رقم = 2)
    if "الشهرين" in norm or "شهرين" in norm:
        end = today
        mo = today.month - 2
        y = today.year
        if mo <= 0:
            mo += 12
            y -= 1
        start = datetime(y, mo, 1)
        return start, end, "آخر شهرين"

    # الأسبوع (هالاسبوع/الاسبوع الحالي = آخر 7 أيام)
    if any(w in norm for w in ["الاسبوع الماضي", "الاسبوع اللي فات", "اسبوع فات"]):
        end = today - timedelta(days=today.weekday() + 1)
        start = end - timedelta(days=6)
        return start, end.replace(hour=23, minute=59, second=59), "الأسبوع الماضي"

    if any(w in norm for w in ["اسبوع", "الاسبوع"]):
        start = today - timedelta(days=7)
        return start, today, "آخر أسبوع"

    # اسم شهر معين مذكور صراحة (مثلاً "اغسطس" أو "بيض يوليو")
    found_month = find_month_in_text(norm)
    if found_month:
        y = today.year
        # لو الشهر المذكور أكبر من الشهر الحالي، غالبًا يقصد السنة الماضية
        if found_month > today.month:
            y -= 1
        start, end = month_range(y, found_month)
        month_name = [k for k, v in MONTHS.items() if v == found_month][0]
        return start, end, month_name

    # الشهر الماضي
    if any(w in norm for w in ["الشهر الماضي", "الشهر اللي فات", "شهر فات"]):
        mo = today.month - 1
        y = today.year
        if mo == 0:
            mo = 12
            y -= 1
        start, end = month_range(y, mo)
        return start, end, "الشهر الماضي"

    # هالشهر / الشهر الحالي
    if any(w in norm for w in ["الشهر", "هالشهر", "شهري"]):
        start, end = month_range(today.year, today.month)
        return start, end, "الشهر الحالي"

    # السنة الماضية
    if any(w in norm for w in ["السنه الماضيه", "العام الماضي", "السنه اللي فاتت"]):
        y = today.year - 1
        return datetime(y, 1, 1), datetime(y, 12, 31, 23, 59, 59), f"سنة {y}"

    # هالسنة / السنة الحالية
    if any(w in norm for w in ["السنه", "هالسنه", "العام"]):
        return datetime(today.year, 1, 1), today, f"سنة {today.year}"

    # كل الفترة / من البداية / كل شي
    if any(w in norm for w in ["كل الفتره", "من البدايه", "كل شي", "الكل", "من الاول"]):
        return datetime(2000, 1, 1), today, "كل الفترة"

    # تاريخ صريح واحد مذكور بالنص (يوم محدد)
    explicit = parse_explicit_date(norm, today.year)
    if explicit:
        end = explicit.replace(hour=23, minute=59, second=59)
        return explicit, end, explicit.strftime("%d/%m/%Y")

    # افتراضي: آخر 30 يوم لو ما انفهم شي محدد
    start = today - timedelta(days=30)
    return start, today, "آخر 30 يوم (افتراضي)"


# ============ فهم نوع الرسالة ============

QUERY_WORDS = ["كم", "كمية", "كميه", "تقرير", "ملخص", "اعطيني", "أعطيني", "شو صار", "وريني", "ورجيني"]
EXPENSE_WORDS = ["مصروف", "مصاريف", "صرفت", "دفعت", "صرف"]
INCOME_WORDS = ["دخل", "مدخول", "بعت", "بيع", "دخلي", "مبيعات"]
EGG_WORDS = ["بيض", "صندوق", "صناديق"]


def is_query(text: str) -> bool:
    norm = normalize_text(text)
    return any(w in norm for w in [normalize_text(w) for w in QUERY_WORDS])


def extract_amount(text: str):
    m = re.search(r"\d+(?:[.,]\d+)?", text)
    if m:
        return float(m.group().replace(",", ""))
    return None


def extract_description(text: str, amount_str: str, keyword_list):
    """يشيل الرقم والكلمات المفتاحية ويرجع الباقي كوصف"""
    cleaned = text
    for kw in keyword_list:
        cleaned = cleaned.replace(kw, "")
    cleaned = re.sub(r"\d+(?:[.,]\d+)?", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "بدون وصف"


# ============ منطق التسجيل ============

def log_egg(quantity: float, date: datetime):
    with open(EGGS_FILE, "a", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerow([date.strftime("%Y-%m-%d"), quantity])


def log_finance(entry_type: str, amount: float, description: str, date: datetime):
    with open(FINANCE_FILE, "a", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerow([date.strftime("%Y-%m-%d"), entry_type, amount, description])


def sum_eggs(start: datetime, end: datetime):
    total = 0
    with open(EGGS_FILE, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            try:
                d = datetime.strptime(row["التاريخ"], "%Y-%m-%d")
                if start <= d <= end:
                    total += float(row["الكمية"])
            except (ValueError, KeyError):
                continue
    return total


def sum_finance(entry_type: str, start: datetime, end: datetime):
    total = 0
    items = []
    with open(FINANCE_FILE, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            try:
                d = datetime.strptime(row["التاريخ"], "%Y-%m-%d")
                if start <= d <= end and row["النوع"] == entry_type:
                    amt = float(row["المبلغ"])
                    total += amt
                    items.append((row["التاريخ"], amt, row["الوصف"]))
            except (ValueError, KeyError):
                continue
    return total, items


# ============ الرد على الرسائل ============

WELCOME = (
    "🐔 <b>أهلاً فيك بمساعد حسابات المزرعة</b> 🐔\n\n"
    "📝 <b>للتسجيل:</b>\n"
    "• \"بيض 100\" أو \"بيض 100 15 اغسطس\"\n"
    "• \"مصروف 50 علف\"\n"
    "• \"دخل 300 بيع بيض\"\n\n"
    "📊 <b>للاستعلام:</b>\n"
    "• \"كمية البيض هالشهر\"\n"
    "• \"تقرير الأسبوع\"\n"
    "• \"المصاريف من 1 لـ 15 اغسطس\"\n"
    "• \"تقرير كل الفترة\"\n\n"
    "بكتبلي وأنا بفهم وبسجل أو برد عليك فورًا ✅"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME, parse_mode="HTML")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    norm = normalize_text(text)

    has_egg = any(w in norm for w in [normalize_text(w) for w in EGG_WORDS])
    has_expense = any(w in norm for w in [normalize_text(w) for w in EXPENSE_WORDS])
    has_income = any(w in norm for w in [normalize_text(w) for w in INCOME_WORDS])
    query = is_query(text)

    # ============ استعلام ============
    if query:
        start_d, end_d, label = parse_period(text)
        reply_parts = [f"📊 <b>تقرير: {label}</b>\n"]

        if has_egg or not (has_expense or has_income):
            eggs_total = sum_eggs(start_d, end_d)
            reply_parts.append(f"🥚 <b>مجموع صناديق البيض:</b> {eggs_total:g}")

        if has_expense or not (has_egg or has_income):
            exp_total, exp_items = sum_finance("مصروف", start_d, end_d)
            reply_parts.append(f"💸 <b>مجموع المصاريف:</b> {exp_total:g}$")

        if has_income or not (has_egg or has_expense):
            inc_total, inc_items = sum_finance("دخل", start_d, end_d)
            reply_parts.append(f"💰 <b>مجموع الدخل:</b> {inc_total:g}$")

        # لو ما في تحديد نوع، اعرض الصافي كمان
        if not (has_egg or has_expense or has_income):
            exp_total, _ = sum_finance("مصروف", start_d, end_d)
            inc_total, _ = sum_finance("دخل", start_d, end_d)
            net = inc_total - exp_total
            reply_parts.append(f"📈 <b>الصافي:</b> {net:g}$")

        await update.message.reply_text("\n".join(reply_parts), parse_mode="HTML")
        return

    # ============ تسجيل بيض ============
    if has_egg:
        amount = extract_amount(text)
        if amount is None:
            await update.message.reply_text("ما لقيت رقم بالرسالة، اكتب مثلاً: بيض 100")
            return
        date = parse_explicit_date(text, datetime.now().year) or datetime.now()
        log_egg(amount, date)
        await update.message.reply_text(
            f"✅ تم تسجيل <b>{amount:g}</b> صندوق بيض بتاريخ <b>{date.strftime('%d/%m/%Y')}</b>",
            parse_mode="HTML",
        )
        return

    # ============ تسجيل مصروف ============
    if has_expense:
        amount = extract_amount(text)
        if amount is None:
            await update.message.reply_text("ما لقيت رقم بالرسالة، اكتب مثلاً: مصروف 50 علف")
            return
        date = parse_explicit_date(text, datetime.now().year) or datetime.now()
        desc = extract_description(text, str(amount), EXPENSE_WORDS)
        log_finance("مصروف", amount, desc, date)
        await update.message.reply_text(
            f"✅ تم تسجيل مصروف <b>{amount:g}$</b> ({desc}) بتاريخ <b>{date.strftime('%d/%m/%Y')}</b>",
            parse_mode="HTML",
        )
        return

    # ============ تسجيل دخل ============
    if has_income:
        amount = extract_amount(text)
        if amount is None:
            await update.message.reply_text("ما لقيت رقم بالرسالة، اكتب مثلاً: دخل 300 بيع بيض")
            return
        date = parse_explicit_date(text, datetime.now().year) or datetime.now()
        desc = extract_description(text, str(amount), INCOME_WORDS)
        log_finance("دخل", amount, desc, date)
        await update.message.reply_text(
            f"✅ تم تسجيل دخل <b>{amount:g}$</b> ({desc}) بتاريخ <b>{date.strftime('%d/%m/%Y')}</b>",
            parse_mode="HTML",
        )
        return

    # ============ ما انفهمت الرسالة ============
    await update.message.reply_text(
        "🤔 ما فهمت قصدك بالضبط.\n"
        "للتسجيل اكتب: بيض/مصروف/دخل + رقم\n"
        "للاستعلام اكتب: كمية/تقرير + الفترة (اليوم/الأسبوع/الشهر...)"
    )


def main():
    ensure_files()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()


if __name__ == "__main__":
    main()
