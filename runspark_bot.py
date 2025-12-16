import os
import logging
import requests
from datetime import datetime, time, timedelta
from telegram import Update, Voice
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters, JobQueue
)
from gtts import gTTS
from io import BytesIO
import pytz

# ============ НАСТРОЙКИ ============
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВСТАВЬ_ТОКЕН_ЗДЕСЬ")
VLADIVOSTOK_TZ = pytz.timezone("Asia/Vladivostok")
VLADIVOSTOK_LAT = 43.1056
VLADIVOSTOK_LON = 131.8735

# Хранилище (в памяти — для продакшена заменить на SQLite/PostgreSQL)
user_profiles = {}  # {user_id: {"name": "...", "last_run": "YYYY-MM-DD", "streak": int}}

# ============ ПОГОДА ============
def get_weather():
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": VLADIVOSTOK_LAT,
        "longitude": VLADIVOSTOK_LON,
        "current": "temperature_2m,weather_code,wind_speed_10m",
        "timezone": "auto"
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        current = data["current"]
        wmo = current["weather_code"]
        wmo_desc = {
            0: "ясно", 1: "преимущественно ясно", 2: "переменная облачность", 3: "облачно",
            51: "слабый дождь", 53: "умеренный дождь", 55: "сильный дождь",
            61: "небольшой дождь", 63: "дождь", 65: "сильный дождь",
            71: "слабый снег", 73: "умеренный снег", 75: "сильный снег",
            95: "гроза"
        }.get(wmo, "неизвестная погода")
        return {
            "temp": current["temperature_2m"],
            "wind": current["wind_speed_10m"],
            "desc": wmo_desc
        }
    except:
        return {"temp": 0, "wind": 0, "desc": "погода недоступна"}

# ============ СЕЗОН ============
def get_season():
    m = datetime.now().month
    if m in [12, 1, 2]: return "зима"
    if m in [3, 4, 5]: return "весна"
    if m in [6, 7, 8]: return "лето"
    return "осень"

# ============ МОТИВАЦИЯ ============
def generate_message(mood: str, weather: dict, season: str, is_sunday: bool):
    distance = 15 if is_sunday else 10
    day_type = "воскресенье" if is_sunday else "будний день"

    # Настроение → вводная фраза
    if any(kw in mood.lower() for kw in ["плох", "устал", "сплю", "не хочу"]):
        intro = "Ты проснулся не потому, что будильник зазвонил. Ты проснулся — потому что внутри тебя ещё жив огонь, который не гасит ни усталость, ни сомнение."
    elif any(kw in mood.lower() for kw in ["норм", "средне", "обычно"]):
        intro = "Привычка сильнее настроения. Ты уже прошёл этот путь сотни раз — и сегодня не исключение."
    else:
        intro = "Сегодня твой день! Мир ждёт твоих километров. Ты чувствуешь — всё складывается."

    # Погода → переосмысление
    if "дождь" in weather["desc"]:
        weather_line = f"Дождь во Владивостоке — не помеха, а союзник. Он смывает сомнения. А {weather['temp']}° — идеально для бега без перегрева."
    elif "снег" in weather["desc"] or weather["temp"] < -3:
        weather_line = f"Мороз и снег — твоя стихия. Каждый вдох — как глоток чистой силы. Зима закаляет не только тело, но и дух."
    elif weather["temp"] > 25:
        weather_line = f"Жара? Отлично! Это шанс проверить, насколько ты стоек. Пот — это твой внутренний огонь, выходящий наружу."
    else:
        weather_line = f"Погода — идеальная: {weather['desc']}, {weather['temp']}°. Природа сама зовёт тебя на пробежку."

    # Сезон → образ
    season_image = {
        "зима": "Ты — один из немногих, кто не прячется от холода. Твои следы на снегу — символ стойкости.",
        "весна": "Природа просыпается — и ты с ней. Каждый шаг — часть возрождения.",
        "лето": "Энергия лета бьёт ключом. Используй её — выжми максимум из этих километров!",
        "осень": "Осень — время сбора урожая. А твой урожай — это километры, пройденные с честью."
    }[season]

    return (
        f"{intro}\n\n"
        f"Сегодня — {day_type}. Твоя цель: **{distance} км**.\n\n"
        f"{weather_line}\n\n"
        f"{season_image}\n\n"
        f"Обувь завязана? Сердце бьётся? Тогда вперёд — не откладывай то, что делает тебя сильнее.\n\n"
        f"Я верю в тебя. А ты?"
    )

# ============ ГОЛОС (TTS) ============
async def send_voice_message(bot, chat_id, text):
    try:
        tts = gTTS(text=text, lang='ru', slow=False)
        audio_bytes = BytesIO()
        tts.write_to_fp(audio_bytes)
        audio_bytes.seek(0)
        await bot.send_voice(chat_id=chat_id, voice=audio_bytes)
    except Exception as e:
        logging.error(f"TTS error: {e}")
        await bot.send_message(chat_id=chat_id, text="🔊 Голосовое сообщение временно недоступно.")

# ============ КОМАНДЫ ============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.first_name
    user_profiles[user_id] = {"name": name}
    await update.message.reply_text(
        f"Привет, {name}! Я — твой утренний мотиватор.\n"
        "Каждое утро в 4:00 я буду спрашивать твоё настроение и присылать мотивацию.\n"
        "Когда пробежишь — отправь /ran"
    )
    # Установить ежедневное напоминание
    context.job_queue.run_daily(
        send_morning_prompt,
        time=time(hour=4, minute=0, second=0),
        timezone=VLADIVOSTOK_TZ,
        user_id=user_id,
        chat_id=update.effective_chat.id
    )

async def send_morning_prompt(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    chat_id = job.chat_id
    user_id = job.user_id

    # Запрос настроения
    await context.bot.send_message(
        chat_id=chat_id,
        text="🌅 Доброе утро! Как твоё настроение? (Напиши: отлично / нормально / плохо)"
    )
    # Ждём ответ 15 минут, потом отправим универсальную мотивацию
    context.job_queue.run_once(
        send_default_motivation,
        when=900,  # 15 минут
        chat_id=chat_id,
        user_id=user_id,
        data={"mood": "не ответил"}
    )

async def send_default_motivation(context: ContextTypes.DEFAULT_TYPE):
    await handle_mood(context, "боевой настрой")  # по умолчанию — сильный тон

async def handle_mood_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mood = update.message.text
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    await handle_mood(context, mood, chat_id, user_id)

async def handle_mood(context, mood, chat_id=None, user_id=None):
    if not chat_id:
        chat_id = context.job.chat_id
        user_id = context.job.user_id

    weather = get_weather()
    season = get_season()
    today = datetime.now(VLADIVOSTOK_TZ)
    is_sunday = today.weekday() == 6  # Воскресенье = 6

    message = generate_message(mood, weather, season, is_sunday)
    await context.bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")
    await send_voice_message(context.bot, chat_id, message)

async def ran(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔥 Отлично! Продолжай в том же духе!")

# ============ ЗАПУСК ============
def main():
    logging.basicConfig(level=logging.INFO)
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ran", ran))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_mood_response))

    app.run_polling()

if __name__ == "__main__":
    main()