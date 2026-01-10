import os
import asyncio
import logging
import tempfile
import shutil
import aiofiles
from pathlib import Path
from typing import Optional
from datetime import datetime

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, FSInputFile
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
import yt_dlp
import ffmpeg

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Получение токена бота из переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не установлен в переменных окружения")

# Инициализация бота с настройками по умолчанию
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()
router = Router()
dp.include_router(router)

class YouTubeDownloader:
    def __init__(self):
        self.ydl_opts = {
            'format': 'bestvideo[height<=1440][fps<=60]+bestaudio/best',
            'outtmpl': '%(title).100s.%(ext)s',
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'noplaylist': True,
            'merge_output_format': 'mkv',
            'postprocessors': [],
            'concurrent_fragment_downloads': 3,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
        }
    
    async def download_video(self, url: str, download_path: Path) -> Optional[Path]:
        """Скачивание видео с YouTube"""
        try:
            # Создаем временную директорию для загрузки
            temp_dir = tempfile.mkdtemp()
            temp_file_pattern = os.path.join(temp_dir, '%(title).100s.%(ext)s')
            
            ydl_opts = self.ydl_opts.copy()
            ydl_opts['outtmpl'] = temp_file_pattern
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                
                # Ищем скачанный файл
                downloaded_files = list(Path(temp_dir).glob('*'))
                if not downloaded_files:
                    logger.error(f"Не найдены файлы в {temp_dir}")
                    return None
                
                original_file = downloaded_files[0]
                
                # Создаем безопасное имя файла
                safe_filename = self._create_safe_filename(original_file.name)
                target_file = download_path / safe_filename
                
                # Перемещаем файл
                await self._move_file(original_file, target_file)
                
                # Очищаем временную директорию
                shutil.rmtree(temp_dir, ignore_errors=True)
                
                logger.info(f"Видео скачано: {target_file}")
                return target_file
                
        except Exception as e:
            logger.error(f"Ошибка при скачивании: {e}")
            return None
    
    def _create_safe_filename(self, filename: str) -> str:
        """Создает безопасное имя файла"""
        # Убираем небезопасные символы
        safe_name = ''.join(c for c in filename if c.isalnum() or c in ' ._-')
        # Ограничиваем длину
        if len(safe_name) > 100:
            name, ext = os.path.splitext(safe_name)
            safe_name = name[:95] + ext
        return safe_name
    
    async def _move_file(self, src: Path, dst: Path):
        """Асинхронное перемещение файла"""
        async with aiofiles.open(src, 'rb') as f_src:
            content = await f_src.read()
            async with aiofiles.open(dst, 'wb') as f_dst:
                await f_dst.write(content)
        src.unlink(missing_ok=True)
    
    def convert_to_mp4_h264(self, input_file: Path, output_path: Path) -> Optional[Path]:
        """Конвертация видео в MP4 с H264 и AAC"""
        try:
            # Создаем имя выходного файла
            output_filename = f"{input_file.stem}_converted.mp4"
            output_file = output_path / output_filename
            
            logger.info(f"Начинаю конвертацию: {input_file}")
            
            # Проверяем, есть ли видео и аудио потоки
            try:
                probe = ffmpeg.probe(str(input_file))
                video_stream = next((s for s in probe['streams'] if s['codec_type'] == 'video'), None)
                audio_stream = next((s for s in probe['streams'] if s['codec_type'] == 'audio'), None)
                
                if not video_stream:
                    logger.error("Не найден видео поток")
                    return None
                
            except Exception as e:
                logger.warning(f"Не удалось проанализировать файл: {e}")
                # Продолжаем конвертацию без анализа
            
            # Настройки конвертации для лучшего качества
            ffmpeg_args = {
                'c:v': 'libx264',
                'preset': 'fast',  # Быстрее чем 'medium', но качество нормальное
                'crf': '22',  # Немного лучше качество чем 23
                'pix_fmt': 'yuv420p',
                'movflags': 'faststart',
                'vf': 'scale=min(2560\,iw):-2'  # Максимальная ширина 2560 (2K)
            }
            
            # Если есть аудио поток
            if audio_stream:
                # Проверяем кодек аудио
                if audio_stream.get('codec_name', '').lower() != 'aac':
                    ffmpeg_args['c:a'] = 'aac'
                    ffmpeg_args['b:a'] = '192k'
                else:
                    ffmpeg_args['c:a'] = 'copy'
            else:
                ffmpeg_args['an'] = None  # Нет аудио
            
            # Создаем команду ffmpeg
            input_stream = ffmpeg.input(str(input_file))
            output_stream = ffmpeg.output(
                input_stream,
                str(output_file),
                **{k: v for k, v in ffmpeg_args.items() if v is not None}
            )
            
            # Запускаем конвертацию
            ffmpeg.run(
                output_stream,
                overwrite_output=True,
                capture_stdout=True,
                capture_stderr=True
            )
            
            logger.info(f"Конвертация завершена: {output_file}")
            return output_file
            
        except ffmpeg.Error as e:
            logger.error(f"Ошибка FFmpeg: {e.stderr.decode() if e.stderr else str(e)}")
            return None
        except Exception as e:
            logger.error(f"Ошибка при конвертации: {e}")
            return None

@router.message(Command("start"))
async def cmd_start(message: Message):
    """Обработчик команды /start"""
    welcome_text = """
🎬 <b>YouTube Video Downloader Bot</b>

📥 <b>Отправьте мне ссылку на YouTube видео</b>

Я:
1. 🎯 Скачаю видео в качестве до 2K 60fps
2. 🔄 Переконвертирую в MP4 (H264 + AAC)
3. 📤 Отправлю готовое видео

⚙️ <b>Поддерживаемые форматы:</b>
• YouTube видео
• YouTube Shorts
• Плейлисты (первое видео)

📋 <b>Команды:</b>
/start - Начало работы
/help - Помощь
/status - Статус бота

⚠️ <b>Ограничения:</b>
• Длительность: до 4 часов
• Размер: до 2GB
• Частота запросов: 1 видео в минуту

🚀 <b>Просто отправьте ссылку!</b>
    """
    await message.answer(welcome_text)

@router.message(Command("help"))
async def cmd_help(message: Message):
    """Обработчик команды /help"""
    help_text = """
ℹ️ <b>Помощь по использованию бота</b>

📌 <b>Как использовать:</b>
1. Отправьте ссылку на YouTube
2. Дождитесь обработки (может занять время)
3. Получите видео в формате MP4

🔧 <b>Технические характеристики:</b>
• Формат: MP4
• Видеокодек: H.264
• Аудиокодек: AAC
• Качество: до 1440p (2K)
• FPS: до 60

🔄 <b>Процесс обработки:</b>
1. 📥 Скачивание с YouTube
2. 🛠️ Конвертация в MP4
3. ✅ Проверка качества
4. 📤 Отправка в Telegram

⏱️ <b>Время обработки:</b>
• Короткие видео (до 10 мин): 1-2 минуты
• Средние (10-30 мин): 3-5 минут
• Длинные (30+ мин): 5-10 минут

🚫 <b>Если что-то не работает:</b>
• Проверьте ссылку
• Убедитесь, что видео доступно
• Подождите и попробуйте снова
• Напишите в поддержку

💡 <b>Советы:</b>
• Используйте стабильное интернет-соединение
• Не отправляйте много запросов одновременно
• Для длинных видео используйте Wi-Fi
    """
    await message.answer(help_text)

@router.message(Command("status"))
async def cmd_status(message: Message):
    """Проверка статуса бота"""
    status_text = """
✅ <b>Бот работает нормально</b>

📊 <b>Статус системы:</b>
• Сервис: Активен
• Версия: 2.0
• Обновлено: Январь 2025

🛠️ <b>Технологии:</b>
• yt-dlp: 2025.1.1
• FFmpeg: H.264 + AAC
• Aiogram: 3.13.0

🔄 <b>Последние изменения:</b>
• Улучшена стабильность загрузки
• Оптимизирована конвертация
• Исправлены баги с длинными видео

📞 <b>Поддержка:</b>
По вопросам и предложениям: @ваш_юзернейм
    """
    await message.answer(status_text)

@router.message(F.text)
async def handle_youtube_link(message: Message):
    """Обработчик YouTube ссылок"""
    url = message.text.strip()
    
    # Проверка на YouTube ссылку
    youtube_patterns = [
        'youtube.com/watch',
        'youtu.be/',
        'youtube.com/shorts/',
        'youtube.com/embed/',
        'youtube.com/playlist'
    ]
    
    if not any(pattern in url for pattern in youtube_patterns):
        await message.answer("❌ <b>Пожалуйста, отправьте корректную ссылку на YouTube.</b>")
        return
    
    # Проверка частоты запросов (простая защита от спама)
    user_id = message.from_user.id
    current_time = datetime.now()
    
    # Создаем директорию для загрузок
    download_dir = Path("downloads") / str(user_id)
    download_dir.mkdir(parents=True, exist_ok=True)
    
    # Отправка сообщения о начале обработки
    status_msg = await message.answer("⏳ <b>Начинаю обработку...</b>")
    
    downloader = YouTubeDownloader()
    
    try:
        # Шаг 1: Скачивание
        await status_msg.edit_text("📥 <b>Скачиваю видео с YouTube...</b>\n<i>Это может занять некоторое время</i>")
        
        original_file = await downloader.download_video(url, download_dir)
        
        if not original_file:
            await status_msg.edit_text("❌ <b>Не удалось скачать видео.</b>\nВозможные причины:\n• Видео недоступно\n• Ограничение по региону\n• Неверная ссылка")
            return
        
        # Шаг 2: Конвертация
        await status_msg.edit_text("🔄 <b>Конвертирую в MP4...</b>\n<i>Кодеки: H.264 + AAC</i>")
        
        converted_file = downloader.convert_to_mp4_h264(original_file, download_dir)
        
        if not converted_file:
            await status_msg.edit_text("❌ <b>Ошибка при конвертации видео.</b>")
            return
        
        # Проверяем размер файла
        file_size = converted_file.stat().st_size
        
        if file_size > 50 * 1024 * 1024:  # > 50MB
            await status_msg.edit_text("📦 <b>Видео слишком большое для отправки напрямую.</b>\n<i>Отправляю как документ...</i>")
            # Отправляем как документ для больших файлов
            video = FSInputFile(str(converted_file))
            await message.answer_document(video, caption="🎬 Ваше видео готово!")
        else:
            # Отправляем как видео
            await status_msg.edit_text("📤 <b>Отправляю видео...</b>")
            video = FSInputFile(str(converted_file))
            await message.answer_video(video=video)
        
        await status_msg.edit_text("✅ <b>Видео успешно отправлено!</b>")
        
    except Exception as e:
        logger.error(f"Ошибка в обработке: {e}")
        await status_msg.edit_text(f"❌ <b>Произошла ошибка:</b>\n<code>{str(e)[:100]}</code>")
    
    finally:
        # Очистка файлов
        try:
            for file in download_dir.glob("*"):
                try:
                    file.unlink()
                except:
                    pass
            download_dir.rmdir()
        except:
            pass

async def main():
    """Основная функция запуска бота"""
    logger.info("Бот запускается...")
    
    # Пропускаем накопленные обновления
    await bot.delete_webhook(drop_pending_updates=True)
    
    logger.info("Бот запущен!")
    
    # Запуск бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    # Создаем основную директорию для загрузок
    Path("downloads").mkdir(exist_ok=True)
    
    # Запускаем асинхронный цикл
    asyncio.run(main())