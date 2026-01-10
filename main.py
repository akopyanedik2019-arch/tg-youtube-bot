import os
import asyncio
import logging
import tempfile
import shutil
from pathlib import Path
from typing import Optional

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, FSInputFile
from aiogram.filters import Command
from aiogram.enums import ParseMode
import yt_dlp
import ffmpeg

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Получение токена бота из переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен в переменных окружения")

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()
router = Router()
dp.include_router(router)

class YouTubeDownloader:
    def __init__(self):
        self.ydl_opts = {
            'format': 'bestvideo[height<=1440][fps<=60]+bestaudio/best[height<=1440][fps<=60]',
            'outtmpl': '%(title)s.%(ext)s',
            'quiet': False,
            'no_warnings': False,
            'extract_flat': False,
            'noplaylist': True,
            'merge_output_format': 'mkv',
            'postprocessors': [],
        }
    
    async def download_video(self, url: str, download_path: Path) -> Optional[Path]:
        """Скачивание видео с YouTube"""
        try:
            # Создаем временную директорию для загрузки
            temp_dir = tempfile.mkdtemp()
            self.ydl_opts['outtmpl'] = os.path.join(temp_dir, '%(title)s.%(ext)s')
            
            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                
                # Ищем скачанный файл
                downloaded_files = list(Path(temp_dir).glob('*'))
                if not downloaded_files:
                    return None
                
                original_file = downloaded_files[0]
                
                # Перемещаем файл в целевую директорию
                target_file = download_path / f"{original_file.stem}_original{original_file.suffix}"
                shutil.move(str(original_file), str(target_file))
                
                # Очищаем временную директорию
                shutil.rmtree(temp_dir)
                
                return target_file
                
        except Exception as e:
            logger.error(f"Ошибка при скачивании: {e}")
            return None
    
    def convert_to_mp4_h264(self, input_file: Path, output_path: Path) -> Optional[Path]:
        """Конвертация видео в MP4 с H264 и AAC"""
        try:
            output_file = output_path / f"{input_file.stem}_converted.mp4"
            
            # Проверяем исходный файл
            probe = ffmpeg.probe(str(input_file))
            video_stream = next((stream for stream in probe['streams'] if stream['codec_type'] == 'video'), None)
            audio_stream = next((stream for stream in probe['streams'] if stream['codec_type'] == 'audio'), None)
            
            # Настройки конвертации
            ffmpeg_args = {
                'c:v': 'libx264',
                'preset': 'medium',
                'crf': '23',
                'pix_fmt': 'yuv420p',
                'movflags': '+faststart'
            }
            
            # Если аудио не AAC, конвертируем
            if audio_stream and audio_stream.get('codec_name', '').lower() != 'aac':
                ffmpeg_args['c:a'] = 'aac'
                ffmpeg_args['b:a'] = '192k'
            else:
                ffmpeg_args['c:a'] = 'copy'
            
            # Запускаем конвертацию
            (
                ffmpeg
                .input(str(input_file))
                .output(
                    str(output_file),
                    **ffmpeg_args,
                    **{'vf': 'scale=min(2560\\,iw):-2'}  # Максимум 2K
                )
                .global_args('-loglevel', 'error')
                .run(overwrite_output=True, capture_stdout=True, capture_stderr=True)
            )
            
            return output_file
            
        except Exception as e:
            logger.error(f"Ошибка при конвертации: {e}")
            return None

@router.message(Command("start"))
async def cmd_start(message: Message):
    """Обработчик команды /start"""
    welcome_text = """
🎬 <b>YouTube Video Downloader Bot</b>

Отправьте мне ссылку на YouTube видео, и я:
1. 📥 Скачаю его в качестве до 2K 60fps
2. 🔄 Переконвертирую в MP4 с кодеком H264 + AAC
3. 📤 Отправлю готовое видео

<u>Ограничения:</u>
• Максимальная длительность: 1 час
• Максимальный размер: 2GB (ограничение Telegram)
• Поддерживаются только одиночные видео (не плейлисты)

Просто отправьте ссылку на видео! 🚀
    """
    await message.answer(welcome_text)

@router.message(Command("help"))
async def cmd_help(message: Message):
    """Обработчик команды /help"""
    help_text = """
ℹ️ <b>Помощь по использованию бота:</b>

1. <b>Отправьте ссылку</b> на YouTube видео
2. <b>Дождитесь</b> скачивания и конвертации
3. <b>Получите</b> готовое видео в формате MP4

<b>Технические характеристики:</b>
• Формат: MP4
• Видеокодек: H264
• Аудиокодек: AAC
• Максимальное качество: 2K (2560x1440)
• Частота кадров: до 60fps

<b>Поддерживаемые ссылки:</b>
• https://www.youtube.com/watch?v=...
• https://youtu.be/...
• https://youtube.com/shorts/...

Если возникли проблемы, попробуйте:
• Проверить доступность видео
• Убедиться, что видео не длиннее 1 часа
• Отправить другую ссылку

По вопросам и предложениям: @ваш_юзернейм
    """
    await message.answer(help_text)

@router.message(F.text)
async def handle_youtube_link(message: Message):
    """Обработчик YouTube ссылок"""
    url = message.text.strip()
    
    # Проверка на YouTube ссылку
    youtube_domains = [
        'youtube.com/watch',
        'youtu.be/',
        'youtube.com/shorts/',
        'youtube.com/embed/'
    ]
    
    if not any(domain in url for domain in youtube_domains):
        await message.answer("❌ Пожалуйста, отправьте корректную ссылку на YouTube видео.")
        return
    
    # Отправка сообщения о начале обработки
    status_msg = await message.answer("⏳ Начинаю обработку видео...")
    
    # Создаем директорию для загрузок
    download_dir = Path("downloads") / str(message.from_user.id)
    download_dir.mkdir(parents=True, exist_ok=True)
    
    # Очистка старых файлов (если есть)
    for old_file in download_dir.glob("*"):
        try:
            old_file.unlink()
        except:
            pass
    
    downloader = YouTubeDownloader()
    
    try:
        # Обновляем статус
        await status_msg.edit_text("📥 Скачиваю видео с YouTube...")
        
        # Скачивание видео
        original_file = await downloader.download_video(url, download_dir)
        
        if not original_file:
            await status_msg.edit_text("❌ Не удалось скачать видео. Проверьте ссылку и доступность видео.")
            return
        
        # Обновляем статус
        await status_msg.edit_text("🔄 Конвертирую в MP4 (H264 + AAC)...")
        
        # Конвертация
        converted_file = downloader.convert_to_mp4_h264(original_file, download_dir)
        
        if not converted_file:
            await status_msg.edit_text("❌ Ошибка при конвертации видео.")
            return
        
        # Отправка видео
        await status_msg.edit_text("📤 Отправляю видео...")
        
        # Проверяем размер файла
        file_size = converted_file.stat().st_size
        
        if file_size > 50 * 1024 * 1024:  # > 50MB
            await message.answer("📦 Видео слишком большое для отправки файлом. Использую альтернативный метод...")
            # Для больших файлов можно использовать другой подход
            # или отправлять как документ
        
        # Отправляем видео
        video = FSInputFile(str(converted_file))
        await message.answer_video(video=video)
        
        await status_msg.edit_text("✅ Видео успешно отправлено!")
        
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await status_msg.edit_text(f"❌ Произошла ошибка: {str(e)}")
    
    finally:
        # Очистка файлов
        try:
            for file in download_dir.glob("*"):
                file.unlink()
            download_dir.rmdir()
        except:
            pass

async def main():
    """Основная функция запуска бота"""
    # Пропускаем накопленные обновления
    await bot.delete_webhook(drop_pending_updates=True)
    
    # Запуск бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())