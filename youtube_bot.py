"""
Telegram-бот для скачивания видео с YouTube.

Установка:
    pip install python-telegram-bot yt-dlp

Также нужен ffmpeg в системе (для склейки видео+аудио):
    Windows: скачать с https://ffmpeg.org и добавить в PATH
    Linux:   sudo apt install ffmpeg
    Mac:     brew install ffmpeg

Запуск:
    1. Получить токен бота у @BotFather в Telegram
    2. Вставить его в переменную BOT_TOKEN ниже (или в .env)
    3. python youtube_bot.py
"""

import os
import logging
import tempfile
import re
import uuid

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
import yt_dlp

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "ВСТАВЬ_СЮДА_СВОЙ_ТОКЕН")

# Лимит на размер файла, который бот может отправить обычным Bot API
MAX_FILE_SIZE_MB = 49

YOUTUBE_REGEX = re.compile(
    r"(https?://)?(www\.)?(youtube\.com|youtu\.be)/\S+"
)

# Варианты качества видео: (подпись на кнопке, format-строка для yt-dlp)
QUALITY_OPTIONS = [
    ("360p", "bestvideo[height<=360]+bestaudio/best[height<=360]"),
    ("480p", "bestvideo[height<=480]+bestaudio/best[height<=480]"),
    ("720p", "bestvideo[height<=720]+bestaudio/best[height<=720]"),
    ("1080p", "bestvideo[height<=1080]+bestaudio/best[height<=1080]"),
]

# Временное хранилище ссылок между выбором пользователя и колбэком.
# Ключ — короткий uuid, значение — исходный URL.
pending_urls: dict[str, str] = {}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Пришли мне ссылку на видео с YouTube — предложу выбрать "
        "качество видео или скачать только аудио (mp3)."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    match = YOUTUBE_REGEX.search(text)

    if not match:
        await update.message.reply_text("Это не похоже на ссылку YouTube. Пришли корректный URL.")
        return

    url = match.group(0)
    request_id = uuid.uuid4().hex[:8]
    pending_urls[request_id] = url

    buttons = [
        InlineKeyboardButton(label, callback_data=f"v|{request_id}|{i}")
        for i, (label, _fmt) in enumerate(QUALITY_OPTIONS)
    ]
    # по 2 кнопки в ряд
    keyboard = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    keyboard.append([InlineKeyboardButton("🎵 Только аудио (MP3)", callback_data=f"a|{request_id}|0")])

    await update.message.reply_text(
        "Выбери качество видео или аудио:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    kind, request_id, index_str = query.data.split("|")
    url = pending_urls.get(request_id)

    if url is None:
        await query.edit_message_text("Ссылка устарела, пришли её заново.")
        return

    await query.edit_message_text("Скачиваю, подожди...")

    with tempfile.TemporaryDirectory() as tmp_dir:
        output_template = os.path.join(tmp_dir, "%(title).80s.%(ext)s")

        if kind == "a":
            ydl_opts = {
                "outtmpl": output_template,
                "format": "bestaudio/best",
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }],
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
            }
        else:
            _, fmt = QUALITY_OPTIONS[int(index_str)]
            ydl_opts = {
                "outtmpl": output_template,
                "format": fmt,
                "merge_output_format": "mp4",
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
            }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                base, _ext = os.path.splitext(filename)
                expected_ext = ".mp3" if kind == "a" else ".mp4"
                if not os.path.exists(filename):
                    filename = base + expected_ext

            file_size_mb = os.path.getsize(filename) / (1024 * 1024)

            if file_size_mb > MAX_FILE_SIZE_MB:
                await query.edit_message_text(
                    f"Файл получился {file_size_mb:.1f}MB — это больше лимита Telegram "
                    f"({MAX_FILE_SIZE_MB}MB). Попробуй качество ниже или аудио вместо видео."
                )
                return

            await query.edit_message_text("Загружаю в Telegram...")
            title = info.get("title", "")

            with open(filename, "rb") as f:
                if kind == "a":
                    await context.bot.send_audio(
                        chat_id=query.message.chat_id,
                        audio=f,
                        title=title,
                        read_timeout=120,
                        write_timeout=120,
                    )
                else:
                    await context.bot.send_video(
                        chat_id=query.message.chat_id,
                        video=f,
                        caption=title,
                        supports_streaming=True,
                        read_timeout=120,
                        write_timeout=120,
                    )
            await query.delete_message()

        except yt_dlp.utils.DownloadError as e:
            logger.error(f"Download error: {e}")
            await query.edit_message_text(
                "Не удалось скачать. Возможно, видео приватное, удалено, "
                "недоступно в вашем регионе, или выбранное качество отсутствует."
            )
        except Exception as e:
            logger.exception("Unexpected error")
            await query.edit_message_text(f"Произошла ошибка: {e}")
        finally:
            pending_urls.pop(request_id, None)


def main():
    if BOT_TOKEN == "ВСТАВЬ_СЮДА_СВОЙ_ТОКЕН":
        raise SystemExit("Укажи BOT_TOKEN в начале файла — получи его у @BotFather")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_choice))

    logger.info("Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
