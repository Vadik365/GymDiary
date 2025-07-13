import sqlite3
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, FSInputFile
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
import asyncio
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
import json
from fpdf import FPDF
import dropbox
from pathlib import Path

load_dotenv()
API_TOKEN = os.getenv('API_TOKEN')
DROPBOX_TOKEN = os.getenv('DROPBOX_TOKEN')
ADMIN_ID = os.getenv("ADMIN_ID")  # ID администратора для уведомлений

dbx = dropbox.Dropbox(DROPBOX_TOKEN)


def upload_to_dropbox(file_path, dropbox_path):
    with open(file_path, "rb") as f:
        dbx.files_upload(f.read(), dropbox_path,
                         mode=dropbox.files.WriteMode.overwrite)
    shared_link_metadata = dbx.sharing_create_shared_link_with_settings(
        dropbox_path)
    return shared_link_metadata.url.replace("?dl=0", "?dl=1")


bot = Bot(token=API_TOKEN, default=DefaultBotProperties(
    parse_mode=ParseMode.HTML))
dp = Dispatcher()

# SQLite
conn = sqlite3.connect("gym_diary.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute('''
CREATE TABLE IF NOT EXISTS trainings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT,
    start_time TEXT,
    end_time TEXT,
    exercises TEXT
)
''')
conn.commit()

# Клавиатуры
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🏋️‍♂️ Записать тренировку")],
        [KeyboardButton(text="✅ Закончить тренировку")],
        [KeyboardButton(text="👀 Просмотреть тренировку")],
        [KeyboardButton(text="📊 Просмотреть отчет")]
    ],
    resize_keyboard=True
)

report_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📅 Отчет за неделю")],
        [KeyboardButton(text="🗓️ Отчет за месяц")],
        [KeyboardButton(text="📄 Отчет в PDF")],
        [KeyboardButton(text="⬅️ Назад")]
    ],
    resize_keyboard=True
)

confirm_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✅ Закончить тренировку?"), KeyboardButton(text="➕ Продолжить тренировку")]],
    resize_keyboard=True,
    one_time_keyboard=True
)

# Временное хранилище сессий
user_sessions = {}


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Привет! Я Gym Diary Bot 💪\nЧто делаем?", reply_markup=main_kb)
    username = message.from_user.username or "<no username>"
    try:
        await bot.send_message(ADMIN_ID, f"👤 Новый пользователь: @{username}\nID: {message.from_user.id}")
    except Exception as e:
        print(f"Ошибка отправки админу: {e}")


@dp.message(Command("users"))
async def list_users(message: types.Message):
    cursor.execute("SELECT DISTINCT user_id, username FROM trainings")
    users = cursor.fetchall()
    if not users:
        await message.answer("Пользователи ещё не зарегистрированы.")
        return

    text = "📋 Список пользователей:\n"
    for uid, uname in users:
        name = f"@{uname}" if uname and uname != "<no username>" else f"ID: {uid}"
        text += f"• {name}\n"
    await message.answer(text)


@dp.message(F.text == "🏋️‍♂️ Записать тренировку")
async def start_training(message: types.Message):
    user_id = message.from_user.id
    user_sessions[user_id] = {
        "start": datetime.now(),
        "exercises": [],
        "buffer": []
    }
    await message.answer("Тренировка начата. Введи упражнение в формате:\n\nЖим лёжа 1x60кг\nЖим гантелей 1x25кг")


@dp.message(F.text.in_({"✅ Закончить тренировку", "✅ Закончить тренировку?"}))
async def finish_training(message: types.Message):
    user_id = message.from_user.id
    session = user_sessions.get(user_id)
    if not session:
        await message.answer("Ты ещё не начинал тренировку!")
        return

    session["exercises"].extend(session["buffer"])
    session["buffer"] = []
    end_time = datetime.now()
    exercises_list = session["exercises"]
    exercises_json = json.dumps(exercises_list)

    cursor.execute("INSERT INTO trainings (user_id, username, start_time, end_time, exercises) VALUES (?, ?, ?, ?, ?)", (
        user_id,
        message.from_user.username or "<no username>",
        session["start"].strftime("%Y-%m-%d %H:%M:%S"),
        end_time.strftime("%Y-%m-%d %H:%M:%S"),
        exercises_json
    ))
    conn.commit()

    start_str = session["start"].strftime("%d.%m %H:%M")
    end_str = end_time.strftime("%H:%M")
    exercises_text = "\n".join(
        f"• {ex}" for ex in exercises_list) if exercises_list else "— без записей"
    del user_sessions[user_id]

    await message.answer(f"Тренировка завершена ✅\n🕒 {start_str} — {end_str}\n{exercises_text}", reply_markup=main_kb)


@dp.message(F.text == "📊 Просмотреть отчет")
async def report_menu(message: types.Message):
    await message.answer("Выбери период отчета:", reply_markup=report_kb)


@dp.message(F.text.in_({"📅 Отчет за неделю", "🗓️ Отчет за месяц"}))
async def report_period(message: types.Message):
    user_id = message.from_user.id
    now = datetime.now()
    if message.text == "📅 Отчет за неделю":
        since = now - timedelta(days=7)
        title = "за неделю"
    else:
        since = now - timedelta(days=30)
        title = "за месяц"

    cursor.execute(
        "SELECT start_time, end_time, exercises FROM trainings WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    filtered = []
    for start, end, ex_json in rows:
        start_dt = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
        if start_dt >= since:
            filtered.append((start_dt, datetime.strptime(
                end, "%Y-%m-%d %H:%M:%S"), json.loads(ex_json)))
    if not filtered:
        await message.answer(f"Нет тренировок {title}.")
        return

    response = f"📊 Отчет {title}:\n\n"
    for i, (start, end, exercises) in enumerate(filtered, 1):
        ex_text = "\n".join(f"• {ex}" for ex in exercises) or "— без записей"
        response += f"<b>{i}) {start.strftime('%d.%m %H:%M')} – {end.strftime('%H:%M')}</b>\n{ex_text}\n\n"
    await message.answer(response)


@dp.message(F.text == "📄 Отчет в PDF")
async def export_monthly_pdf(message: types.Message):
    user_id = message.from_user.id
    now = datetime.now()
    since = now - timedelta(days=30)
    cursor.execute(
        "SELECT start_time, end_time, exercises FROM trainings WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    filtered = []
    for start, end, ex_json in rows:
        start_dt = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
        if start_dt >= since:
            filtered.append((start_dt, datetime.strptime(
                end, "%Y-%m-%d %H:%M:%S"), json.loads(ex_json)))
    if not filtered:
        await message.answer("Нет данных для экспорта за последний месяц.")
        return

    pdf = FPDF()
    pdf.add_page()
    # 👇 Абсолютный путь к шрифту
    font_path = Path(__file__).parent / "DejaVuSans.ttf"
    print("Font path:", font_path.resolve())
    pdf.add_font("DejaVu", "", str(font_path), uni=True)
    pdf.set_font("DejaVu", size=12)

    username_display = f"@{message.from_user.username}" if message.from_user.username else f"ID {user_id}"
    pdf.cell(
        0, 10, txt=f"Отчет о тренировках пользователя {username_display} за последний месяц", ln=True, align='C')
    for i, (start, end, exercises) in enumerate(filtered, 1):
        pdf.ln(5)
        pdf.cell(
            0, 10, txt=f"{i}) {start.strftime('%d.%m %H:%M')} — {end.strftime('%H:%M')}", ln=True)
        for ex in exercises:
            pdf.cell(0, 10, txt=f"   • {ex}", ln=True)

    file_path = f"training_report_{user_id}_month.pdf"
    pdf.output(file_path)
    await message.answer_document(FSInputFile(file_path), caption="📄 Вот твой отчет за последний месяц")

    try:
        link = upload_to_dropbox(file_path, f"/reports/{file_path}")
        await message.answer(f"☁️ Отчет также загружен в Dropbox:\n{link}")
    except Exception as e:
        print(f"Dropbox ошибка: {e}")
    try:
        os.remove(file_path)
    except Exception as e:
        print(f"Ошибка удаления файла: {e}")


@dp.message(F.text == "⬅️ Назад")
async def back_to_main(message: types.Message):
    await message.answer("Главное меню:", reply_markup=main_kb)


@dp.message(F.text == "➕ Продолжить тренировку")
async def continue_training(message: types.Message):
    await message.answer("Продолжай вводить упражнения ⬇️", reply_markup=ReplyKeyboardRemove())


@dp.message()
async def log_exercise(message: types.Message):
    user_id = message.from_user.id
    session = user_sessions.get(user_id)
    if session:
        session["buffer"].append(message.text)
        await message.answer("Добавить в дневник?", reply_markup=confirm_kb)
    else:
        await message.answer("Если хочешь записать тренировку, нажми «🏋️‍♂️ Записать тренировку»")


async def main():
    print("🤖 Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
