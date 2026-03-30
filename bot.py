from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timedelta

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import BotCommand, Message

from storage import BirthdayRecord, BirthdayStorage


LOGGER = logging.getLogger(__name__)
ROUTER = Router()
DATE_HELP_TEXT = "Формат даты: `ДД.ММ` или `ДД.ММ.ГГГГ`."

SETTINGS: dict[str, object] = {}
STORAGE: BirthdayStorage | None = None
REMINDER_TASK: asyncio.Task[None] | None = None


def load_settings() -> dict[str, object]:
    token = os.getenv("BOT_TOKEN", "8752404476:AAHCbFAz4N5tTFVEjiJhy8m7Tp9r3DW0fPs").strip()
    if not token:
        raise RuntimeError("Не найден BOT_TOKEN.")

    return {
        "token": token,
        "storage_file": os.getenv("STORAGE_FILE", "birthdays.json").strip() or "birthdays.json",
        "reminder_days": int(os.getenv("REMINDER_DAYS", "3")),
        "reminder_hour": int(os.getenv("REMINDER_HOUR", "9")),
    }


def get_storage() -> BirthdayStorage:
    if STORAGE is None:
        raise RuntimeError("БД не инициализирован.")
    return STORAGE


def normalize_name(text: str) -> str:
    name = " ".join(text.split())
    if not name:
        raise ValueError("Имя не может быть пустым.")
    if len(name) > 80:
        raise ValueError("Имя слишком длинное. Максимум 80 символов.")
    return name


def parse_date(text: str) -> tuple[int, int, int | None]:
    parts = text.strip().split(".")
    if len(parts) not in (2, 3) or not all(part.isdigit() for part in parts):
        raise ValueError("Неверный формат даты.")

    day = int(parts[0])
    month = int(parts[1])

    if len(parts) == 2:
        date(2000, month, day)
        return day, month, None

    year = int(parts[2])
    date(year, month, day)
    return day, month, year


def parse_add_payload(text: str) -> tuple[str, tuple[int, int, int | None]]:
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        raise ValueError("Использование: /add <имя> <дата>")

    payload = parts[1].strip()
    if " " not in payload:
        raise ValueError("Укажите имя и дату.")

    raw_name, raw_date = payload.rsplit(maxsplit=1)
    return normalize_name(raw_name), parse_date(raw_date)


def safe_birthday(day: int, month: int, year: int) -> date:
    try:
        return date(year, month, day)
    except ValueError:
        if day == 29 and month == 2:
            return date(year, 2, 28)
        raise


def next_birthday(today: date, record: BirthdayRecord) -> date:
    current_year = safe_birthday(record.day, record.month, today.year)
    if current_year >= today:
        return current_year
    return safe_birthday(record.day, record.month, today.year + 1)


def format_record_date(record: BirthdayRecord) -> str:
    if record.year is not None:
        return f"{record.day:02d}.{record.month:02d}.{record.year}"
    return f"{record.day:02d}.{record.month:02d}"


def days_word(value: int) -> str:
    number = abs(value) % 100
    if 11 <= number <= 14:
        return "дней"

    tail = number % 10
    if tail == 1:
        return "день"
    if 2 <= tail <= 4:
        return "дня"
    return "дней"


def format_days_left(days_left: int) -> str:
    if days_left == 0:
        return "сегодня"
    return f"через {days_left} {days_word(days_left)}"


def help_text(reminder_days: int) -> str:
    return (
        "Команды:\n"
        "/add <имя> <дата> - добавить день рождения\n"
        "/delete <имя> - удалить запись\n"
        "/list - показать список\n"
        "/help - помощь\n\n"
        "Примеры:\n"
        "/add Анна 15.04\n"
        "/add Иван Петров 02.11.2004\n"
        "/delete Анна\n\n"
        f"Напоминания: за {reminder_days} дня(дней) и в день рождения."
    )


@ROUTER.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Привет! Я простой бот дней рождения.\n\n"
        + help_text(int(SETTINGS["reminder_days"]))
    )


@ROUTER.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(help_text(int(SETTINGS["reminder_days"])))


@ROUTER.message(Command("add"))
async def cmd_add(message: Message) -> None:
    if message.text is None or message.from_user is None:
        return

    try:
        name, (day, month, year) = parse_add_payload(message.text)
    except ValueError as error:
        await message.answer(f"Ошибка: {error}\n{DATE_HELP_TEXT}\nПример: /add Анна 15.04")
        return

    record = BirthdayRecord(name=name, day=day, month=month, year=year)
    if not get_storage().add_birthday(message.from_user.id, record):
        await message.answer("Такое имя уже есть в списке.")
        return

    await message.answer(f"Сохранено: {name} - {format_record_date(record)}")


@ROUTER.message(Command("delete"))
async def cmd_delete(message: Message) -> None:
    if message.text is None or message.from_user is None:
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /delete <имя>")
        return

    try:
        name = normalize_name(parts[1])
    except ValueError as error:
        await message.answer(f"Ошибка: {error}")
        return

    if not get_storage().remove_birthday(message.from_user.id, name):
        await message.answer("Запись не найдена.")
        return

    await message.answer(f"Удалено: {name}")


@ROUTER.message(Command("list"))
async def cmd_list(message: Message) -> None:
    if message.from_user is None:
        return

    records = get_storage().get_user_birthdays(message.from_user.id)
    if not records:
        await message.answer("Список пуст. Добавьте запись через /add.")
        return

    today = datetime.now().date()
    rows: list[tuple[int, str, BirthdayRecord]] = []
    for record in records:
        days_left = (next_birthday(today, record) - today).days
        rows.append((days_left, record.name.casefold(), record))

    rows.sort(key=lambda item: (item[0], item[1]))

    lines = ["Ваши дни рождения:"]
    for index, (days_left, _, record) in enumerate(rows, start=1):
        lines.append(
            f"{index}. {record.name} - {format_record_date(record)} ({format_days_left(days_left)})"
        )

    await message.answer("\n".join(lines))


@ROUTER.message(F.text.startswith("/"))
async def cmd_unknown(message: Message) -> None:
    await message.answer("Неизвестная команда. Используйте /help.")


async def send_reminders(bot: Bot) -> None:
    reminder_days = int(SETTINGS["reminder_days"])
    today = datetime.now().date()

    for user_id, records in get_storage().get_all_users().items():
        for record in records:
            upcoming = next_birthday(today, record)
            days_left = (upcoming - today).days
            if days_left not in (0, reminder_days):
                continue

            age_text = ""
            if record.year is not None:
                age_text = f" Исполнится {upcoming.year - record.year} лет."

            if days_left == 0:
                text = f"Сегодня день рождения у {record.name} ({format_record_date(record)})!{age_text}"
            else:
                text = (
                    f"Напоминание: через {days_left} {days_word(days_left)} "
                    f"день рождения у {record.name} ({format_record_date(record)}).{age_text}"
                )

            try:
                await bot.send_message(chat_id=user_id, text=text)
            except TelegramForbiddenError:
                LOGGER.warning("Нет доступа к чату пользователя %s", user_id)
            except TelegramAPIError as error:
                LOGGER.exception("Ошибка отправки напоминания пользователю %s: %s", user_id, error)


async def reminder_loop(bot: Bot) -> None:
    while True:
        now = datetime.now()
        target = now.replace(
            hour=int(SETTINGS["reminder_hour"]),
            minute=0,
            second=0,
            microsecond=0,
        )
        if target <= now:
            target += timedelta(days=1)

        await asyncio.sleep(max(1, int((target - now).total_seconds())))

        try:
            await send_reminders(bot)
        except Exception:
            LOGGER.exception("Ошибка в напоминаниях.")


async def on_startup(bot: Bot) -> None:
    global REMINDER_TASK
    await bot.set_my_commands(
        [
            BotCommand(command="add", description="Добавить день рождения"),
            BotCommand(command="delete", description="Удалить запись"),
            BotCommand(command="list", description="Показать список"),
            BotCommand(command="help", description="Справка"),
            BotCommand(command="start", description="Старт"),
        ]
    )
    REMINDER_TASK = asyncio.create_task(reminder_loop(bot))


async def on_shutdown(bot: Bot) -> None:
    del bot
    global REMINDER_TASK
    if REMINDER_TASK is None:
        return

    REMINDER_TASK.cancel()
    try:
        await REMINDER_TASK
    except asyncio.CancelledError:
        pass


async def run_bot() -> None:
    global SETTINGS, STORAGE
    SETTINGS = load_settings()
    STORAGE = BirthdayStorage(str(SETTINGS["storage_file"]))

    bot = Bot(token=str(SETTINGS["token"]))
    dispatcher = Dispatcher()
    dispatcher.include_router(ROUTER)
    dispatcher.startup.register(on_startup)
    dispatcher.shutdown.register(on_shutdown)

    LOGGER.info("Бот запущен.")
    await dispatcher.start_polling(bot)


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.INFO,
    )
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
