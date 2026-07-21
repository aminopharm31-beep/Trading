"""
بوت تيليغرام لتنبيهات أسعار الكريبتو (Binance Spot)
- /price SYMBOL          : عرض السعر الحالي
- /alert SYMBOL PRICE    : تنبيه عند وصول السعر (فوق أو تحت حسب السعر الحالي)
- /alerts                : عرض التنبيهات النشطة
- /remove ID             : حذف تنبيه
- /start /help           : المساعدة
"""

import json
import logging
import os
from pathlib import Path

import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ------------------------- الإعدادات -------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "ضع_التوكن_هنا")  # من BotFather
CHECK_INTERVAL_SECONDS = 30  # كل كم ثانية يفحص البوت الأسعار
BINANCE_TICKER_URL = "https://api.binance.com/api/v3/ticker/price"
DATA_FILE = Path(__file__).parent / "alerts.json"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ------------------------- تخزين التنبيهات -------------------------
def load_alerts() -> list:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return []


def save_alerts(alerts: list) -> None:
    DATA_FILE.write_text(json.dumps(alerts, ensure_ascii=False, indent=2), encoding="utf-8")


def next_id(alerts: list) -> int:
    return (max((a["id"] for a in alerts), default=0)) + 1


# ------------------------- Binance -------------------------
def get_price(symbol: str) -> float | None:
    """يرجع السعر الحالي لزوج تداول من Binance (مثال: BTCUSDT)."""
    try:
        resp = requests.get(BINANCE_TICKER_URL, params={"symbol": symbol.upper()}, timeout=10)
        if resp.status_code != 200:
            return None
        return float(resp.json()["price"])
    except Exception as e:
        logger.error(f"Error fetching price for {symbol}: {e}")
        return None


# ------------------------- أوامر البوت -------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أهلًا 👋\n\n"
        "الأوامر المتاحة:\n"
        "/price BTCUSDT - عرض السعر الحالي\n"
        "/alert BTCUSDT 65000 - تعيين تنبيه عند وصول السعر\n"
        "/alerts - عرض التنبيهات النشطة\n"
        "/remove ID - حذف تنبيه\n\n"
        "⚠️ هذا بوت تنبيهات فقط، لا ينفّذ أي عملية شراء أو بيع."
    )


async def price_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("استخدم الأمر هكذا: /price BTCUSDT")
        return

    symbol = context.args[0].upper()
    price = get_price(symbol)
    if price is None:
        await update.message.reply_text(f"لم أجد السعر لـ {symbol}. تأكد من صحة الرمز (مثال: BTCUSDT).")
        return

    await update.message.reply_text(f"💰 سعر {symbol} الآن: {price}")


async def alert_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("استخدم الأمر هكذا: /alert BTCUSDT 65000")
        return

    symbol = context.args[0].upper()
    try:
        target_price = float(context.args[1])
    except ValueError:
        await update.message.reply_text("السعر المُدخل غير صحيح.")
        return

    current_price = get_price(symbol)
    if current_price is None:
        await update.message.reply_text(f"لم أجد السعر لـ {symbol}. تأكد من صحة الرمز.")
        return

    direction = "above" if target_price > current_price else "below"

    alerts = load_alerts()
    alert = {
        "id": next_id(alerts),
        "chat_id": update.effective_chat.id,
        "symbol": symbol,
        "target_price": target_price,
        "direction": direction,  # "above" = ينبّه عند الصعود فوق السعر، "below" = عند الهبوط تحته
    }
    alerts.append(alert)
    save_alerts(alerts)

    arrow = "⬆️" if direction == "above" else "⬇️"
    await update.message.reply_text(
        f"✅ تم تعيين تنبيه #{alert['id']}\n"
        f"{symbol} {arrow} {target_price}\n"
        f"(السعر الحالي: {current_price})"
    )


async def alerts_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    alerts = load_alerts()
    my_alerts = [a for a in alerts if a["chat_id"] == update.effective_chat.id]

    if not my_alerts:
        await update.message.reply_text("لا توجد تنبيهات نشطة حاليًا.")
        return

    lines = ["📋 تنبيهاتك النشطة:\n"]
    for a in my_alerts:
        arrow = "⬆️" if a["direction"] == "above" else "⬇️"
        lines.append(f"#{a['id']} - {a['symbol']} {arrow} {a['target_price']}")
    await update.message.reply_text("\n".join(lines))


async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("استخدم الأمر هكذا: /remove ID (رقم التنبيه من /alerts)")
        return

    try:
        alert_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("رقم التنبيه غير صحيح.")
        return

    alerts = load_alerts()
    new_alerts = [a for a in alerts if not (a["id"] == alert_id and a["chat_id"] == update.effective_chat.id)]

    if len(new_alerts) == len(alerts):
        await update.message.reply_text("لم أجد تنبيهًا بهذا الرقم.")
        return

    save_alerts(new_alerts)
    await update.message.reply_text(f"🗑️ تم حذف التنبيه #{alert_id}")


# ------------------------- مهمة فحص الأسعار الدورية -------------------------
async def check_alerts_job(context: ContextTypes.DEFAULT_TYPE):
    alerts = load_alerts()
    if not alerts:
        return

    # جلب الأسعار لكل الرموز الفريدة مرة واحدة لتقليل عدد الطلبات
    symbols = {a["symbol"] for a in alerts}
    prices = {s: get_price(s) for s in symbols}

    remaining_alerts = []
    for a in alerts:
        current_price = prices.get(a["symbol"])
        if current_price is None:
            remaining_alerts.append(a)
            continue

        triggered = (
            (a["direction"] == "above" and current_price >= a["target_price"])
            or (a["direction"] == "below" and current_price <= a["target_price"])
        )

        if triggered:
            try:
                await context.bot.send_message(
                    chat_id=a["chat_id"],
                    text=(
                        f"🔔 تنبيه! {a['symbol']} وصل إلى {current_price}\n"
                        f"(الهدف كان: {a['target_price']})"
                    ),
                )
            except Exception as e:
                logger.error(f"Failed to send alert: {e}")
            # لا نعيد إضافته - التنبيه يُستهلك بعد تفعيله مرة واحدة
        else:
            remaining_alerts.append(a)

    save_alerts(remaining_alerts)


# ------------------------- تشغيل البوت -------------------------
def main():
    if BOT_TOKEN == "ضع_التوكن_هنا":
        raise SystemExit(
            "يرجى تعيين متغير البيئة BOT_TOKEN بتوكن البوت الخاص بك من BotFather."
        )

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("price", price_cmd))
    app.add_handler(CommandHandler("alert", alert_cmd))
    app.add_handler(CommandHandler("alerts", alerts_cmd))
    app.add_handler(CommandHandler("remove", remove_cmd))

    # فحص دوري للتنبيهات
    app.job_queue.run_repeating(check_alerts_job, interval=CHECK_INTERVAL_SECONDS, first=10)

    logger.info("Bot started.")
    app.run_polling()


if __name__ == "__main__":
    main()
