
import os
import uuid
import subprocess
import threading
from flask import Flask, send_from_directory, abort

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

# --- ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
PUBLIC_URL = os.environ.get("RENDER_EXTERNAL_URL")  # Render автоматически подставляет
if not PUBLIC_URL:
    PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://127.0.0.1:8080")  # fallback для локала

# --- ПАПКА ДЛЯ ВИДЕО ---
BASE_DIR = "videos"
os.makedirs(BASE_DIR, exist_ok=True)

# --- FLASK ---
app = Flask(__name__)

@app.route("/")
def index():
    return "Бот работает! Используй Telegram для управления."

@app.route("/download/<filename>")
def download(filename):
    path = os.path.join(BASE_DIR, filename)
    if not os.path.exists(path):
        abort(404)
    return send_from_directory(BASE_DIR, filename, as_attachment=True)

# --- TELEGRAM BOT ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 Привет! Пришли ссылку на YouTube.\n"
        "Я предложу варианты качества и дам прямую ссылку на MP4 (H.264 + AAC)."
    )

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    context.user_data["url"] = url

    keyboard = [
        [InlineKeyboardButton("🔥 4K (2160p)", callback_data="2160")],
        [InlineKeyboardButton("⚡ 1440p", callback_data="1440")],
        [InlineKeyboardButton("🎯 1080p", callback_data="1080")],
        [InlineKeyboardButton("🤖 Лучшее доступное", callback_data="best")]
    ]

    await update.message.reply_text(
        "Выбери качество:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def download_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    url = context.user_data.get("url")
    if not url:
        await query.edit_message_text("❌ Ссылка потерялась. Пришли её заново.")
        return

    quality = query.data
    uid = uuid.uuid4().hex
    filename = f"{uid}.mp4"
    filepath = os.path.join(BASE_DIR, filename)

    await query.edit_message_text("⏳ Качаю и собираю видео…")

    if quality == "best":
        fmt = "bestvideo[vcodec=h264]+bestaudio[acodec=aac]/best"
    else:
        fmt = f"bestvideo[vcodec=h264][height<={quality}]+bestaudio[acodec=aac]/best"

    try:
        subprocess.run(
            ["yt-dlp", url, "-f", fmt, "--merge-output-format", "mp4", "-o", filepath],
            check=True,
            timeout=900
        )
        link = f"{PUBLIC_URL}/download/{filename}"
        await query.edit_message_text(
            f"✅ Готово!\n\n📥 Прямая ссылка:\n{link}\n\n⚠️ Ссылка временная."
        )
    except subprocess.TimeoutExpired:
        await query.edit_message_text(
            "⏱️ Видео слишком большое или загрузка заняла слишком много времени.\n"
            "Попробуй выбрать меньшее качество."
        )
    except Exception:
        await query.edit_message_text(
            "❌ Ошибка загрузки.\n"
            "Видео может быть недоступно или ограничено."
        )

def run_bot():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не найден! Проверь Environment Variables.")
    app_bot = ApplicationBuilder().token(BOT_TOKEN).build()
    app_bot.add_handler(CommandHandler("start", start))
    app_bot.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app_bot.add_handler(CallbackQueryHandler(download_video))
    app_bot.run_polling()

# --- ЗАПУСК FLASK + BOT ---
if __name__ == "__main__":
    threading.Thread(target=run_bot).start()
    app.run(host="0.0.0.0", port=8080)
