
import os
import re
import logging
import asyncio
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram import F
from yt_dlp import YoutubeDL

logging.basicConfig(level=logging.INFO)

TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_HOST = os.environ["WEBHOOK_HOST"]  # например https://your-bot.onrender.com
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
PORT = int(os.environ.get("PORT", 8080))

bot = Bot(token=TOKEN, parse_mode="HTML")
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

class States(StatesGroup):
    waiting_quality = State()

def sizeof_fmt(num, suffix="B"):
    if num is None:
        return "неизвестно"
    for unit in ["", "K", "M", "G", "T"]:
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}P{suffix}"

ydl_opts = {
    "quiet": True,
    "no_warnings": True,
    "extract_flat": False,
}

def get_progressive_formats(info):
    formats = []
    for f in info.get("formats", []):
        if (f.get("ext") == "mp4" and
            f.get("vcodec", "").startswith("avc1.") and
            f.get("acodec", "").startswith("mp4a.") and
            f.get("vcodec") != "none" and
            f.get("acodec") != "none"):
            height = f.get("height") or 0
            fps = f.get("fps") or 30
            size = f.get("filesize") or f.get("filesize_approx")
            size_str = sizeof_fmt(size)
            label = f"{height}p"
            if fps and fps != 30:
                label += f" {fps}fps"
            label += f" ≈ {size_str}"
            formats.append({
                "format_id": f["format_id"],
                "label": label,
                "height": height,
            })
    formats.sort(key=lambda x: x["height"], reverse=True)
    return formats

youtube_regex = re.compile(r"(https?://)?(www\.)?(youtube|youtu)\.(com|be)/?.*")

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Привет! Отправь ссылку на YouTube-видео, я дам прямые ссылки на скачивание в MP4 H.264 + AAC в разных качествах.")

@dp.message(F.text.regexp(youtube_regex))
async def handle_youtube(message: types.Message, state: FSMContext):
    url = message.text.strip()
    await message.answer("Анализирую видео...")

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if info.get("entries"):
            await message.answer("Плейлисты не поддерживаются. Отправь ссылку на одно видео.")
            return

        title = info.get("title", "Видео").replace("<", "").replace(">", "")
        pro_formats = get_progressive_formats(info)

        if not pro_formats:
            await message.answer("Нет доступных форматов MP4 H.264 + AAC (progressive). "
                                 "Видео, вероятно, только в высоком качестве с отдельными потоками.")
            return

        kb = InlineKeyboardMarkup(inline_keyboard=[])
        for fmt in pro_formats:
            kb.inline_keyboard.append([InlineKeyboardButton(
                text=fmt["label"],
                callback_data=f"quality:{fmt['format_id']}"
            )])

        await state.set_state(States.waiting_quality)
        await state.update_data(video_url=url, title=title)

        await message.answer(f"Доступные качества для <b>{title}</b>:\nВыбери:", reply_markup=kb)

    except Exception as e:
        logging.exception("Ошибка при извлечении информации")
        await message.answer("Ошибка: неверная ссылка или видео недоступно.")

@dp.callback_query(F.data.startswith("quality:"))
async def send_direct_link(callback: types.CallbackQuery, state: FSMContext):
    format_id = callback.data.split(":", 1)[1]
    data = await state.get_data()
    video_url = data["video_url"]
    title = data.get("title", "video")

    await callback.message.edit_text("Генерирую свежую ссылку...")

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)

        target_format = next((f for f in info["formats"] if f["format_id"] == format_id), None)
        if not target_format:
            await callback.message.edit_text("Формат больше недоступен.")
            return

        dl_url = target_format["url"]
        # Добавляем подсказку имени файла для браузера/менеджера загрузок
        safe_title = "".join(c if c.isalnum() or c in " _-" else "_" for c in title)
        dl_url += f"&title={safe_title}.mp4"

        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text="📥 Скачать видео прямо сейчас",
            url=dl_url
        )]])

        await callback.message.edit_text(
            f"Готовая прямая ссылка на <b>{title}</b>:\n"
            "Ссылка работает несколько часов. Кликай и скачивай!",
            reply_markup=kb
        )
        await state.clear()

    except Exception as e:
        logging.exception("Ошибка при генерации ссылки")
        await callback.message.edit_text("Не удалось получить ссылку. Попробуй позже или другое видео.")

# Webhook-сервер
async def on_startup():
    await bot.delete_webhook(drop_pending=True)
    await bot.set_webhook(WEBHOOK_URL)
    logging.info(f"Webhook установлен: {WEBHOOK_URL}")

async def on_shutdown():
    await bot.delete_webhook()
    await bot.session.close()

app = web.Application()

async def handle_webhook(request):
    update = await request.json()
    await dp.feed_update(bot=bot, update=types.Update(**update))
    return web.Response()

app.router.add_post(WEBHOOK_PATH, handle_webhook)
app.on_startup.append(on_startup)
app.on_shutdown.append(on_shutdown)

if __name__ == "__main__":
    web.run_app(app, host="0.0.0.0", port=PORT)
