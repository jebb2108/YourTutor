import asyncio
import logging
import sys
import sqlite3
import os
from typing import List, Tuple, Optional

# Основные компоненты aiogram для работы с Telegram API
from aiogram import Bot, Dispatcher, F  # F - фильтры для обработки сообщений
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode  # Режимы форматирования текста
from aiogram.filters import Command, CommandStart  # Фильтры команд
from aiogram.fsm.context import FSMContext  # Контекст машины состояний
from aiogram.fsm.state import State, StatesGroup, default_state  # Система состояний
from aiogram.fsm.storage.memory import MemoryStorage  # Хранилище состояний в памяти
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton  # Типы данных Telegram

# Импорт конфигурационных данных (токен бота и сообщения)
from messages import *

# Инициализация бота и диспетчера
TOKEN = KEY  # Секретный ключ из файла messages.py
storage = MemoryStorage()  # Хранилище состояний (в оперативной памяти)
dp = Dispatcher(storage=storage)  # Центральный диспетчер для обработки событий

"""
Классы состояний (Finite State Machine):
Как сценарий в игре - запоминают где находится пользователь в процессе взаимодействия.
Например: 
1. Начальное состояние -> 2. Добавление слова -> 3. Выбор части речи

StatesGroup - контейнеры для связанных состояний
"""


class WordStates(StatesGroup):
    """Состояния для добавления нового слова"""
    waiting_for_part_of_speech = State()  # Ожидание выбора части речи


class WordsViewState(StatesGroup):
    """Состояния для просмотра словаря"""
    viewing_words = State()  # Режим просмотра слов


class EditState(StatesGroup):
    """Состояния для редактирования слов"""
    waiting_edit_word = State()  # Ожидание нового слова
    waiting_edit_pos = State()  # Ожидание части речи
    waiting_edit_value = State()  # Ожидание значения


"""
Работа с базой данных:
Каждый пользователь хранит слова в своей SQLite базе.
Путь к файлу: dbs/dictionary_12345.db (где 12345 - ID пользователя)

Принцип работы:
1. При первом обращении создается файл БД
2. Все операции выполняются в отдельных соединениях
3. Для асинхронной работы используем обычные функции (SQLite не поддерживает асинхронность)
"""


def get_user_db_path(user_id: int) -> str:
    """Генерирует путь к персональной базе данных пользователя"""
    return f'dbs/dictionary_{user_id}.db'


def ensure_user_db(user_id: int):
    """Создает базу данных и таблицу при первом обращении"""
    db_path = get_user_db_path(user_id)
    if not os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(CREATE_TABLE)  # SQL-запрос из messages.py
        conn.commit()
        conn.close()
        logging.info(f"Created new database for user {user_id}")


async def get_words_from_db(user_id: int) -> List[Tuple[str, str, str]]:
    """Получает все слова пользователя из базы (асинхронная обертка)"""
    ensure_user_db(user_id)
    db_path = get_user_db_path(user_id)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT word, part_of_speech, translation FROM words ORDER BY word")
        return cursor.fetchall()  # Возвращает список кортежей (слово, часть_речи, перевод)
    except sqlite3.Error as e:
        logging.error(f"Database error: {e}")
        return []
    finally:
        conn.close()


async def delete_word_from_db(user_id: int, word: str) -> bool:
    """Удаляет слово из базы данных пользователя"""
    ensure_user_db(user_id)
    db_path = get_user_db_path(user_id)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM words WHERE word = ?", (word,))
        conn.commit()
        return cursor.rowcount > 0  # True если удаление прошло успешно
    except sqlite3.Error as e:
        logging.error(f"Database error: {e}")
        return False
    finally:
        conn.close()


async def update_word_in_db(user_id: int, old_word: str, new_word: str, pos: str, value: str) -> bool:
    """Обновляет слово в базе данных (с проверкой изменения слова)"""
    ensure_user_db(user_id)
    db_path = get_user_db_path(user_id)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        if old_word != new_word:
            # Если слово изменилось - удаляем старую и создаем новую запись
            cursor.execute("DELETE FROM words WHERE word = ?", (old_word,))
            cursor.execute("""
                INSERT INTO words (word, part_of_speech, translation)
                VALUES (?, ?, ?)
            """, (new_word, pos, value))
        else:
            # Если слово не менялось - обновляем остальные поля
            cursor.execute("""
                UPDATE words 
                SET part_of_speech = ?, translation = ?
                WHERE word = ?
            """, (pos, value, new_word))
        conn.commit()
        return cursor.rowcount > 0
    except sqlite3.Error as e:
        logging.error(f"Database error: {e}")
        return False
    finally:
        conn.close()


async def add_word_to_db(user_id: int, word: str, pos: str, value: str) -> bool:
    """Добавляет новое слово в базу данных пользователя"""
    ensure_user_db(user_id)
    db_path = get_user_db_path(user_id)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute(INSERT_WORD, (word, pos, value))  # INSERT_WORD из messages.py
        conn.commit()
        return cursor.rowcount > 0
    except sqlite3.Error as e:
        logging.error(f"Database error: {e}")
        return False
    finally:
        conn.close()


async def check_word_exists(user_id: int, word: str) -> bool:
    """Проверяет существует ли слово в базе пользователя"""
    ensure_user_db(user_id)
    db_path = get_user_db_path(user_id)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute(SELECT_WORD, (word,))  # SELECT_WORD из messages.py
        return cursor.fetchone() is not None
    except sqlite3.Error as e:
        logging.error(f"Database error: {e}")
        return False
    finally:
        conn.close()


"""
Обработчики команд и сообщений:
Сердце бота - функции, реагирующие на действия пользователя.

Принцип работы:
1. Telegram сервер отправляет событие (сообщение, нажатие кнопки)
2. Диспетчер (dp) находит подходящий обработчик
3. Выполняется асинхронная функция (корутина)
4. Бот отправляет ответ

Ключевые элементы декораторов:
- @dp.message(Command("words")): реагирует на команду /words
- @dp.callback_query(F.data == ...): обрабатывает нажатие кнопки
"""


@dp.message(Command("words"))
async def show_dictionary(message: Message, state: FSMContext):
    """Обработка команды /words - показывает словарь пользователя"""
    user_id = message.from_user.id
    words = await get_words_from_db(user_id)

    if not words:
        await message.answer("📭 Your dictionary is empty. Add some words first!")
        return

    # Сохраняем слова в контексте состояния
    await state.update_data(
        words=words,
        current_index=0,
        current_letter=words[0][0][0].upper() if words[0][0] else 'A'
    )

    # Показываем первое слово
    await show_current_word(message, state)
    await state.set_state(WordsViewState.viewing_words)


async def show_current_word(message: Message, state: FSMContext, edit: bool = False):
    """
    Отображает текущее слово с навигацией

    Параметр edit определяет:
    - False: отправить новое сообщение
    - True: редактировать существующее сообщение
    """
    data = await state.get_data()
    words = data.get("words", [])
    current_index = data.get("current_index", 0)

    if not words or current_index >= len(words):
        await message.answer("❌ No words found")
        await state.clear()
        return

    word, pos, value = words[current_index]

    # Форматируем сообщение с HTML-разметкой
    text = (
        f"📖 <b>Word</b>: {word}{' ' * (70 - len(word))}{current_index + 1} out of {len(words)} 🔢\n"
        f"🔤 <b>Part of speech:</b> {pos}\n"
    )
    if value:
        text += f"💡 <b>Meaning:</b> {value[:50]+'...' if len(value) > 50 else value}\n"

    # Создаем интерактивную клавиатуру с действиями
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Info", callback_data="show_info")],
        [InlineKeyboardButton(text="⬅️", callback_data="prev_word"),
         InlineKeyboardButton(text="➡️", callback_data="next_word")],
        [InlineKeyboardButton(text="⬆️ Letter", callback_data="prev_letter"),
         InlineKeyboardButton(text="Letter ⬇️", callback_data="next_letter")],
        [InlineKeyboardButton(text="✏️ Edit", callback_data="edit_word"),
         InlineKeyboardButton(text="🗑️ Delete", callback_data="delete_word")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel_words")]
    ])

    # Отправляем или редактируем сообщение
    if edit:
        await message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    else:
        await message.answer(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


"""
Обработка нажатий кнопок:
Когда пользователь нажимает inline-кнопку, Telegram отправляет CallbackQuery

Принцип работы:
1. Кнопка создается с callback_data="действие"
2. Обработчик регистрируется через @dp.callback_query(F.data == "действие")
3. В обработчике:
   - Обновляем состояние
   - Меняем интерфейс
   - Отвечаем на callback (callback.answer())
"""


@dp.callback_query(F.data == "prev_word", WordsViewState.viewing_words)
async def prev_word_handler(callback: CallbackQuery, state: FSMContext):
    """Обработка кнопки 'Предыдущее слово'"""
    data = await state.get_data()
    current_index = data.get("current_index", 0)

    if current_index > 0:
        await state.update_data(current_index=current_index - 1)
        await show_current_word(callback.message, state, edit=True)
    else:
        await callback.answer("You're at the first word")

    await callback.answer()  # Обязательно подтверждаем обработку


@dp.callback_query(F.data == "next_word", WordsViewState.viewing_words)
async def next_word_handler(callback: CallbackQuery, state: FSMContext):
    """Обработка кнопки 'Следующее слово'"""
    data = await state.get_data()
    words = data.get("words", [])
    current_index = data.get("current_index", 0)

    if current_index < len(words) - 1:
        await state.update_data(current_index=current_index + 1)
        await show_current_word(callback.message, state, edit=True)
    else:
        await callback.answer("You're at the last word")

    await callback.answer()


@dp.callback_query(F.data == "prev_letter", WordsViewState.viewing_words)
async def prev_letter_handler(callback: CallbackQuery, state: FSMContext):
    """Переход к первой букве в предыдущей группе слов"""
    data = await state.get_data()
    words = data.get("words", [])
    current_index = data.get("current_index", 0)
    current_letter = data.get("current_letter", 'A')

    # Получаем уникальные буквы из слов
    letters = sorted(set(word[0][0].upper() for word in words if word[0] and len(word[0]) > 0))

    if not letters:
        await callback.answer("No letters found")
        return

    # Находим текущую позицию буквы в алфавите
    try:
        current_pos = letters.index(current_letter)
        new_pos = max(0, current_pos - 1)
        new_letter = letters[new_pos]
    except ValueError:
        new_letter = letters[0]

    # Ищем первое слово на новую букву
    new_index = next((i for i, word in enumerate(words)
                      if word[0] and word[0][0].upper() == new_letter), 0)

    await state.update_data(
        current_index=new_index,
        current_letter=new_letter
    )
    await show_current_word(callback.message, state, edit=True)
    await callback.answer()


@dp.callback_query(F.data == "next_letter", WordsViewState.viewing_words)
async def next_letter_handler(callback: CallbackQuery, state: FSMContext):
    """Переход к первой букве в следующей группе слов"""
    data = await state.get_data()
    words = data.get("words", [])
    current_index = data.get("current_index", 0)
    current_letter = data.get("current_letter", 'A')

    # Получаем уникальные буквы из слов
    letters = sorted(set(word[0][0].upper() for word in words if word[0] and len(word[0]) > 0))

    if not letters:
        await callback.answer("No letters found")
        return

    # Находим текущую позицию буквы в алфавите
    try:
        current_pos = letters.index(current_letter)
        new_pos = min(len(letters) - 1, current_pos + 1)
        new_letter = letters[new_pos]
    except ValueError:
        new_letter = letters[-1]

    # Ищем первое слово на новую букву
    new_index = next((i for i, word in enumerate(words)
                      if word[0] and word[0][0].upper() == new_letter), 0)

    await state.update_data(
        current_index=new_index,
        current_letter=new_letter
    )
    await show_current_word(callback.message, state, edit=True)
    await callback.answer()


@dp.callback_query(F.data == "cancel_words", WordsViewState.viewing_words)
async def cancel_words_handler(callback: CallbackQuery, state: FSMContext):
    """Выход из режима просмотра слов"""
    await callback.message.delete()  # Удаляем сообщение с навигацией
    await state.clear()  # Сбрасываем состояние
    await callback.answer()


@dp.callback_query(F.data == "delete_word", WordsViewState.viewing_words)
async def delete_word_handler(callback: CallbackQuery, state: FSMContext):
    """Удаление текущего слова из базы данных"""
    user_id = callback.from_user.id
    data = await state.get_data()
    words = data.get("words", [])
    current_index = data.get("current_index", 0)

    if not words or current_index >= len(words):
        await callback.answer("No word to delete")
        return

    word, _, _ = words[current_index]

    # Удаляем слово из базы
    if await delete_word_from_db(user_id, word):
        # Обновляем список слов
        words = await get_words_from_db(user_id)

        if not words:
            # Если словарь пуст - выходим из режима просмотра
            await callback.message.edit_text("✅ Word deleted\n")
            await state.clear()
            return

        # Корректируем текущий индекс
        new_index = current_index if current_index < len(words) else len(words) - 1
        new_letter = words[new_index][0][0].upper() if words[new_index][0] else 'A'

        await state.update_data(
            words=words,
            current_index=new_index,
            current_letter=new_letter
        )

        # Обновляем интерфейс
        await show_current_word(callback.message, state, edit=True)
        await callback.answer(f"✅ {word} deleted")
    else:
        await callback.answer(f"❌ Failed to delete {word}")


@dp.callback_query(F.data == "edit_word", WordsViewState.viewing_words)
async def start_edit_word(callback: CallbackQuery, state: FSMContext):
    """Начало процесса редактирования слова"""
    data = await state.get_data()
    words = data.get("words", [])
    current_index = data.get("current_index", 0)

    if not words or current_index >= len(words):
        await callback.answer("No word to edit")
        return

    word, pos, value = words[current_index]

    # Сохраняем текущие значения для возможного сравнения
    await state.update_data(
        editing_word=word,
        editing_pos=pos,
        editing_value=value,
        editing_index=current_index,
        original_word=word,
        original_pos=pos,
        original_value=value
    )

    # Клавиатура выбора редактируемого поля
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✏️ Word", callback_data="edit_word_text"),
            InlineKeyboardButton(text="💡 Meaning", callback_data="edit_word_value")
        ],
        [
            InlineKeyboardButton(text="🔤 Part of Speech", callback_data="edit_word_pos")
        ],
        [InlineKeyboardButton(text="↩️ Back", callback_data="cancel_edit")]
    ])

    await callback.message.edit_text(
        f"✏️ <b>Editing:</b> {word}\n"
        f"🔤 <b>Current POS:</b> {pos}\n"
        f"💡 <b>Current Meaning:</b> {value or 'None'}\n\n"
        "Select what to edit:",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )
    await state.set_state(EditState.waiting_edit_word)


@dp.callback_query(F.data.startswith("edit_word_"), EditState.waiting_edit_word)
async def handle_edit_choice(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора поля для редактирования"""
    edit_type = callback.data.replace("edit_word_", "")
    data = await state.get_data()
    word = data.get("editing_word", "")

    if edit_type == "text":
        await callback.message.edit_text(f"✏️ Enter new text for <b>{word}</b>:", parse_mode=ParseMode.HTML)
        await state.set_state(EditState.waiting_edit_word)
    elif edit_type == "value":
        await callback.message.edit_text(f"💡 Enter new meaning for <b>{word}</b>:", parse_mode=ParseMode.HTML)
        await state.set_state(EditState.waiting_edit_value)
    elif edit_type == "pos":
        # Клавиатура с выбором части речи
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Noun", callback_data="newpos_noun"),
             InlineKeyboardButton(text="Verb", callback_data="newpos_verb")],
            [InlineKeyboardButton(text="Adjective", callback_data="newpos_adjective"),
             InlineKeyboardButton(text="Adverb", callback_data="newpos_adverb")],
            [InlineKeyboardButton(text="↩️ Back", callback_data="cancel_edit")]
        ])
        await callback.message.edit_text(f"🔤 Select new part of speech for <b>{word}</b>:",
                                         reply_markup=keyboard,
                                         parse_mode=ParseMode.HTML)
        await state.set_state(EditState.waiting_edit_pos)

    await callback.answer()


@dp.callback_query(F.data == "cancel_edit", EditState.waiting_edit_word)
@dp.callback_query(F.data == "cancel_edit", EditState.waiting_edit_value)
@dp.callback_query(F.data == "cancel_edit", EditState.waiting_edit_pos)
async def cancel_edit_handler(callback: CallbackQuery, state: FSMContext):
    """Отмена редактирования и возврат к просмотру"""
    await state.set_state(WordsViewState.viewing_words)
    await show_current_word(callback.message, state, edit=True)
    await callback.answer()


@dp.message(EditState.waiting_edit_word)
async def handle_edit_word_text(message: Message, state: FSMContext):
    """Обработка нового текста слова"""
    user_id = message.from_user.id
    new_word = message.text.strip()
    data = await state.get_data()
    old_word = data.get("editing_word", "")
    original_word = data.get("original_word", "")

    # Проверка на дубликаты (если слово изменилось)
    if new_word != original_word:
        words = await get_words_from_db(user_id)
        if any(w[0].lower() == new_word.lower() for w in words):
            await message.answer("⚠️ This word already exists in the dictionary")
            return

    # Обновляем данные и сохраняем
    await state.update_data(editing_word=new_word)
    await save_edited_word(message, state, user_id)


@dp.message(EditState.waiting_edit_value)
async def handle_edit_word_value(message: Message, state: FSMContext):
    """Обработка нового значения слова"""
    new_value = message.text.strip()
    await state.update_data(editing_value=new_value)
    await save_edited_word(message, state, message.from_user.id)


@dp.callback_query(F.data.startswith("newpos_"), EditState.waiting_edit_pos)
async def handle_edit_word_pos(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора новой части речи"""
    new_pos = callback.data.replace("newpos_", "")
    await state.update_data(editing_pos=new_pos)
    await save_edited_word(callback.message, state, callback.from_user.id)
    await callback.answer()


async def save_edited_word(message: Message, state: FSMContext, user_id: int):
    """Сохранение изменений слова в базе данных"""
    data = await state.get_data()
    # Текущие значения из состояния
    new_word = data.get("editing_word", "")
    new_pos = data.get("editing_pos", "")
    new_value = data.get("editing_value", "")

    # Оригинальные значения (для сравнения)
    original_word = data.get("original_word", "")
    original_pos = data.get("original_pos", "")
    original_value = data.get("original_value", "")

    editing_index = data.get("editing_index", 0)

    # Проверка на наличие изменений
    if (new_word == original_word and
            new_pos == original_pos and
            new_value == original_value):
        await message.answer("ℹ️ No changes detected")
        await state.set_state(WordsViewState.viewing_words)
        await show_current_word(message, state, edit=True)
        return

    # Обновляем слово в базе
    success = await update_word_in_db(user_id, original_word, new_word, new_pos, new_value)
    if success:
        # Обновляем список слов
        words = await get_words_from_db(user_id)

        # Находим новую позицию слова
        new_index = next((i for i, w in enumerate(words) if w[0] == new_word), editing_index)

        await state.update_data(
            words=words,
            current_index=new_index
        )

        # Возвращаемся к просмотру
        await state.set_state(WordsViewState.viewing_words)
        await show_current_word(message, state, edit=True)
    else:
        await message.answer("❌ Failed to update word")
        await state.set_state(WordsViewState.viewing_words)
        await show_current_word(message, state, edit=True)

    """
    Процесс добавления новых слов:
    1. Пользователь отправляет слово (или слово:значение)
    2. Бот переводит в состояние ожидания части речи
    3. Пользователь выбирает часть речи
    4. Бот сохраняет слово в БД
    
    FSMContext - контекст состояния, хранящий данные между шагами
    """

@ dp.message(CommandStart())
async def start_command_handler(message: Message):

    """Обработка команды /start - приветствие"""
    await message.answer(f"👋 Hello, {message.from_user.first_name}! {GREETING}", parse_mode=ParseMode.HTML)

@dp.message(WordStates.waiting_for_part_of_speech)
async def handle_part_of_speech_text(message: Message):
    """Напоминание использовать кнопки при вводе текста вместо выбора части речи"""
    await message.answer("⚠️ Please select a part of speech from the buttons above")

@dp.callback_query(F.data.startswith("pos_"), WordStates.waiting_for_part_of_speech)
async def save_new_word_handler(callback: CallbackQuery, state: FSMContext) -> None:
    """Сохранение нового слова после выбора части речи"""
    user_id = callback.from_user.id
    part_of_speech = callback.data.replace("pos_", "")
    data = await state.get_data()
    word = data.get("word")
    value = data.get("value")

    # Сохраняем в БД
    if await add_word_to_db(user_id, word, part_of_speech, value):
        # Формируем сообщение об успехе
        response = f"✅ Saved: {word} ({part_of_speech})"
        if value:
            response += f"\nMeaning: {value[:50] + '...' if len(value) > 50 else value}"

        await callback.message.edit_text(response)
        await callback.answer()
        await state.clear()  # Выходим из состояния
    else:
        await callback.message.edit_text("❌ Failed to save word")
        await callback.answer()

@dp.message()
async def universal_message_handler(message: Message, state: FSMContext):
    """
    Универсальный обработчик текстовых сообщений

    Логика работы:
    1. Пропускаем команды (они обрабатываются отдельно)
    2. Проверяем текущее состояние пользователя
    3. В зависимости от состояния:
       - Если в процессе добавления слова: просим использовать кнопки
       - Если в режиме редактирования: пропускаем
       - Иначе: начинаем процесс добавления нового слова
    """
    # Игнорируем команды
    if message.text.startswith('/'):
        return

    # Получаем текущее состояние пользователя
    current_state = await state.get_state()

    # Если пользователь должен выбрать часть речи
    if current_state == WordStates.waiting_for_part_of_speech.state:
        await handle_part_of_speech_text(message)
        return

    # Если в режиме редактирования - пропускаем
    if current_state in [
        EditState.waiting_edit_word.state,
        EditState.waiting_edit_pos.state,
        EditState.waiting_edit_value.state
    ]:
        return

    # Начинаем процесс добавления нового слова
    await process_word_input(message, state)

async def process_word_input(message: Message, state: FSMContext):
    """Обработка ввода нового слова"""
    user_id = message.from_user.id
    text = message.text.strip()

    # Проверяем формат "слово:значение"
    if ':' in text:
        parts = text.split(':', 1)
        word = parts[0].strip()
        value = parts[1].strip() if parts[1].strip() else None
    else:
        word, value = text, None

    # Проверяем дубликаты
    if await check_word_exists(user_id, word):
        await message.answer("⚠️ Word already exists")
        await state.clear()
        return

    # Сохраняем временные данные в состоянии
    await state.update_data(word=word, value=value)

    # Создаем клавиатуру с частями речи
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Noun", callback_data="pos_noun"),
         InlineKeyboardButton(text="Verb", callback_data="pos_verb")],
        [InlineKeyboardButton(text="Adjective", callback_data="pos_adjective"),
         InlineKeyboardButton(text="Adverb", callback_data="pos_adverb")]
    ])

    # Переводим пользователя в состояние выбора части речи
    await message.answer("❓ What part of speech is it?", reply_markup=keyboard)
    await state.set_state(WordStates.waiting_for_part_of_speech)

"""
Запуск бота:
asyncio.run() - запускает асинхронную среду выполнения
bot.start_polling() - бесконечный цикл опроса серверов Telegram

Как это работает:
1. Создаем экземпляр бота
2. Запускаем диспетчер в режиме опроса
3. Бот начинает получать обновления от серверов Telegram
4. Каждое обновление обрабатывается в отдельной асинхронной задаче
"""

async def main() -> None:
    """Точка входа в асинхронное приложение"""
    # Инициализация бота с HTML-форматированием по умолчанию
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    # Запуск обработки входящих сообщений
    await dp.start_polling(bot)

if __name__ == "__main__":
    # Настройка логирования
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
    )

    # Запуск асинхронного приложения
    asyncio.run(main())