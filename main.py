
import os
import re
import logging
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
WEBHOOK_HOST = os.environ["WEBHOOK_HOST"]
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
PORT = int(os.environ.get("PORT", 8080))

bot = Bot(token=TOKEN)
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
    "geo_bypass": True,
    "http_headers": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "Referer": "https://www.youtube.com/",
        "Accept-Language": "en-US,en;q=0.9",
    },
}

def get_progressive_formats(info):
    formats = []
    for f in info.get("formats", []):
        if (f.get("ext") == "mp4" and
            f.get("vcodec", "").startswith("avc1.") and
            f.get("acodec", "").startswith("mp4a.") and
            f.get("acodec") != "none"):
            height = f.get("height") or 0
            fps = f.get("fps") or 30
            size = f.get("filesize") or f.get("filesize_approx")
            size_str = sizeof_fmt(size)
            label = f"{height}p"
            if fps != 30:
                label += f" {fps}fps"
            label += f" ≈ {size_str} (готовый с аудио)"
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
    await message.answer(
        "Привет! Кидай ссылку на YouTube — дам максимум H264 + AAC в прямых ссылках.\n"
        "Для высокого качества (1080p+) — видео и аудио отдельно (объедини в VLC).",
        parse_mode="HTML"
    )

@dp.message(F.text.regexp(youtube_regex))
async def handle_youtube(message: types.Message, state: FSMContext):
    url = message.text.strip()
    await message.answer("Анализирую видео... (обхожу блокировки)", parse_mode="HTML")

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if info.get("entries"):
            await message.answer("Плейлисты не поддерживаются. Только одно видео.", parse_mode="HTML")
            return

        title = info.get("title", "Видео").replace("<", "").replace(">", "")

        pro_formats = get_progressive_formats(info)

        kb = InlineKeyboardMarkup(inline_keyboard=[])

        has_high = False
        high_video_id = None
        high_audio_id = None

        if pro_formats:
            for fmt in pro_formats:
                kb.inline_keyboard.append([InlineKeyboardButton(
                    text=fmt["label"],
                    callback_data=f"quality:{fmt['format_id']}"
                )])

        # Ищем лучшее video-only H264 выше progressive
        video_only = [f for f in info["formats"] if f.get("ext") == "mp4" and f.get("vcodec", "").startswith("avc1.") and f.get("acodec") == "none" and f.get("height")]
        if video_only:
            video_only.sort(key=lambda x: x["height"], reverse=True)
            max_pro_height = max((f["height"] for f in pro_formats), default=0)
            if video_only[0]["height"] > max_pro_height:
                has_high = True
                high_video_id = video_only[0]["format_id"]

                # Лучший аудио
                audio_formats = [f for f in info["formats"] if f.get("acodec", "").startswith("mp4a.") and f.get("vcodec") == "none"]
                if audio_formats:
                    audio_formats.sort(key=lambda x: x.get("abr", 0), reverse=True)
                    high_audio_id = audio_formats[0]["format_id"]

        if has_high and high_audio_id:
            kb.inline_keyboard.append([InlineKeyboardButton(
                text=f"🔥 Лучшее H264 {video_only[0]['height']}p (видео + аудио отдельно)",
                callback_data="high_quality"
            )])

        if not kb.inline_keyboard:
            await message.answer("Нет форматов H264 + AAC. Видео в других кодеках или заблокировано.", parse_mode="HTML")
            return

        await state.set_state(States.waiting_quality)
        await state.update_data(video_url=url, title=title, high_video_id=high_video_id, high_audio_id=high_audio_id)

        await message.answer(f"Доступно для <b>{title}</b>:\nВыбери вариант:", reply_markup=kb, parse_mode="HTML")

    except Exception as e:
        err_str = str(e).lower()
        if "age" in err_str or "restricted" in err_str or "confirm" in err_str:
            await message.answer("18+ видео. YouTube блочит, даже с обходом не всегда проходит. Попробуй без ?si= или другое.", parse_mode="HTML")
        else:
            logging.exception("yt-dlp error")
            await message.answer("Ошибка YouTube. Попробуй позже или чистую ссылку без ?si=", parse_mode="HTML")

@dp.callback_query(F.data.startswith("quality:"))
async def send_progressive_link(callback: types.CallbackQuery, state: FSMContext):
    format_id = callback.data.split(":", 1)[1]
    data = await state.get_data()
    video_url = data["video_url"]
    title = data["title"]

    await callback.message.edit_text("Генерирую прямую ссылку...", parse_mode="HTML")

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)

        target = next((f for f in info["formats"] if f["format_id"] == format_id), None)
        if not target:
            await callback.message.edit_text("Формат пропал. Попробуй заново.", parse_mode="HTML")
            return

        dl_url = target["url"]
        safe_title = "".join(c if c.isalnum() or c in " _-" else "_" for c in title)
        dl_url += f"&title={safe_title}.mp4"

        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📥 Скачать готовый MP4", url=dl_url)]])

        await callback.message.edit_text(f"Готово <b>{title}</b>:\nСсылка на несколько часов.", reply_markup=kb, parse_mode="HTML")
        await state.clear()

    except Exception:
        await callback.message.edit_text("Ошибка ссылки. Попробуй заново.", parse_mode="HTML")

@dp.callback_query(F.data == "high_quality")
async def send_high_links(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    video_url = data["video_url"]
    title = data["title"]
    high_video_id = data.get("high_video_id")
    high_audio_id = data.get("high_audio_id")

    if not high_video_id or not high_audio_id:
        await callback.message.edit_text("Высокое качество пропало. Попробуй заново.", parse_mode="HTML")
        return

    await callback.message.edit_text("Генерирую ссылки на лучшее H264 + AAC...", parse_mode="HTML")

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)

        video_fmt = next((f for f in info["formats"] if f["format_id"] == high_video_id), None)
        audio_fmt = next((f for f in info["formats"] if f["format_id"] == high_audio_id), None)

        if not video_fmt or not audio_fmt:
            await callback.message.edit_text("Форматы пропали.", parse_mode="HTML")
            return

        video_url = video_fmt["url"]
        audio_url = audio_fmt["url"]

        safe_title = "".join(c if c.isalnum() or c in " _-" else "_" for c in title)

        video_url += f"&title={safe_title}_video.mp4"
        audio_url += f"&title={safe_title}_audio.m4a"

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"📥 Видео {video_fmt.get('height', '')}p (без звука)", url=video_url)],
            [InlineKeyboardButton(text="📥 Аудио AAC (лучшее)", url=audio_url)]
        ])

        await callback.message.edit_text(
            f"Лучшее H264 для <b>{title}</b>:\n"
            "Скачай ОБА файла и объедини (в VLC: Медиа > Открыть несколько файлов или online merger).\n"
            "Ссылки на несколько часов.",
            reply_markup=kb,
            parse_mode="HTML"
        )
        await state.clear()

    except Exception:
        await callback.message.edit_text("Ошибка ссылок. Попробуй заново.", parse_mode="HTML")

async def on_startup(app):
    try:
        info = await bot.get_webhook_info()
        if info.url != WEBHOOK_URL:
            await bot.delete_webhook()
            await bot.set_webhook(url=WEBHOOK_URL)
            logging.info("Webhook обновлён")
        else:
            logging.info("Webhook ок")
    except Exception as e:
        logging.error(f"Webhook: {e}")
        await bot.delete_webhook()
        await bot.set_webhook(url=WEBHOOK_URL)

async def on_shutdown(app):
    try:
        await bot.delete_webhook()
        await bot.session.close()
    except Exception as e:
        logging.error(f"Shutdown: {e}")

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
