import asyncio
import itertools
import logging
import os
import random
from dataclasses import dataclass, field

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiohttp import web
from webapp import make_app
from aiogram.types import (
    WebAppInfo,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

TOKEN = os.getenv("BOT_TOKEN", "PUT_YOUR_TOKEN_HERE")
WEBAPP_URL = os.getenv("WEBAPP_URL", "")  # masalan: https://xxx.up.railway.app

MIN_GROUP = 5          # guruh uchun minimal odam
MAX_GROUP = 6          # maksimal odam
GROUP_WAIT_SECONDS = 20  # 5 kishi bo'lganda kutish vaqti

SPEAKING_QUESTIONS = [
    "Part 1: Tell me about your hometown. What do you like most about it?",
    "Part 1: Do you prefer studying in the morning or in the evening? Why?",
    "Part 1: How often do you use social media? What do you use it for?",
    "Part 1: What kind of music do you enjoy listening to?",
    "Part 2: Describe a person who has influenced you. Say who this person is, how you know them, and why they influenced you.",
    "Part 2: Describe a place you would like to visit. Say where it is, what you would do there, and why you want to go.",
    "Part 2: Describe a book or film you enjoyed. Say what it was about and why you liked it.",
    "Part 3: Do you think technology has made people's lives easier? Why?",
    "Part 3: Why do some people prefer to live abroad?",
    "Part 3: How important is it for young people to learn foreign languages?",
]

DEBATE_TOPICS = [
    "People nowadays tend to have children at older ages. Do the advantages outweigh the disadvantages?",
    "Private health care should be abolished and replaced by free public health care.",
    "Online learning is better than classroom learning.",
    "Governments should spend more on public transport than on building new roads.",
    "Social media does more harm than good to teenagers.",
    "University education should be free for everyone.",
    "Children should start learning a foreign language at primary school.",
]

logging.basicConfig(level=logging.INFO)
router = Router()

# ---------------- In-memory holat ----------------
pair_queue: list[int] = []
partner: dict[int, int] = {}

group_queue: list[int] = []
group_timer: asyncio.Task | None = None

_room_ids = itertools.count(1)


@dataclass
class Room:
    id: int
    topic: str
    members: list[int] = field(default_factory=list)


rooms: dict[int, Room] = {}
room_of: dict[int, int] = {}
names: dict[int, str] = {}


# ---------------- Yordamchi funksiyalar ----------------
def kb(*rows):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t, callback_data=d) for t, d in row]
            for row in rows
        ]
    )


def busy(uid: int) -> bool:
    return (
        uid in partner
        or uid in room_of
        or uid in pair_queue
        or uid in group_queue
    )


def menu_text() -> str:
    return (
        "🎓 <b>IELTS Speaking Club</b>\n\n"
        "📊 <b>Dashboard</b>\n"
        f"🗣 1-on-1 navbatda: <b>{len(pair_queue)}</b>\n"
        f"💬 Suhbatdagi juftliklar: <b>{len(partner) // 2}</b>\n"
        f"👥 Guruh navbatida: <b>{len(group_queue)}/{MAX_GROUP}</b>\n"
        f"🔥 Faol debate xonalari: <b>{len(rooms)}</b>\n\n"
        "Bo'limni tanlang:"
    )


_rows = []
if WEBAPP_URL:
    _rows.append([InlineKeyboardButton(text="🎙 Live 1-on-1 (ovozli)", web_app=WebAppInfo(url=WEBAPP_URL))])
    _rows.append([InlineKeyboardButton(text="🎙 Live Group Debate (5-6 kishi)", web_app=WebAppInfo(url=WEBAPP_URL + "?mode=group"))])
_rows += [
    [InlineKeyboardButton(text="🗣 1-on-1 (voice xabar)", callback_data="pair:join")],
    [InlineKeyboardButton(text="👥 Group Discussion (Debate)", callback_data="group:join")],
    [InlineKeyboardButton(text="🔄 Dashboardni yangilash", callback_data="menu")],
]
MENU_KB = InlineKeyboardMarkup(inline_keyboard=_rows)


async def send_menu(bot: Bot, uid: int):
    await bot.send_message(uid, menu_text(), reply_markup=MENU_KB, parse_mode="HTML")


# ---------------- Start / menyu ----------------
@router.message(CommandStart())
@router.message(Command("menu"))
async def cmd_start(m: Message):
    names[m.from_user.id] = m.from_user.first_name or "User"
    await send_menu(m.bot, m.from_user.id)


@router.callback_query(F.data == "menu")
async def cb_menu(cb: CallbackQuery):
    await cb.answer("Yangilandi")
    try:
        await cb.message.edit_text(menu_text(), reply_markup=MENU_KB, parse_mode="HTML")
    except Exception:
        pass


@router.message(Command("stop"))
async def cmd_stop(m: Message):
    uid = m.from_user.id
    await leave_everything(m.bot, uid)
    await send_menu(m.bot, uid)


async def leave_everything(bot: Bot, uid: int):
    if uid in pair_queue:
        pair_queue.remove(uid)
    if uid in group_queue:
        group_queue.remove(uid)
    if uid in partner:
        other = partner.pop(uid)
        partner.pop(other, None)
        await bot.send_message(other, "❌ Sherigingiz suhbatni tugatdi.")
        await send_menu(bot, other)
    if uid in room_of:
        await leave_room(bot, uid)


# ---------------- 1-on-1 Speaking ----------------
@router.callback_query(F.data == "pair:join")
async def pair_join(cb: CallbackQuery):
    uid = cb.from_user.id
    names[uid] = cb.from_user.first_name or "User"
    if busy(uid):
        await cb.answer("Avval joriy sessiyani tugating (/stop)", show_alert=True)
        return
    await cb.answer()

    if pair_queue:
        other = pair_queue.pop(0)
        partner[uid] = other
        partner[other] = uid
        q = random.choice(SPEAKING_QUESTIONS)
        text = (
            "✅ <b>Sherik topildi!</b>\n\n"
            "Endi voice (🎤) yoki matn yuboring — xabarlar anonim yetkaziladi.\n\n"
            f"❓ <b>Savol:</b>\n{q}"
        )
        controls = kb(
            [("➡️ Keyingi savol", "pair:next")],
            [("🚪 Tugatish", "pair:leave")],
        )
        for u in (uid, other):
            await cb.bot.send_message(u, text, reply_markup=controls, parse_mode="HTML")
    else:
        pair_queue.append(uid)
        await cb.message.edit_text(
            "⏳ Sherik qidirilmoqda...",
            reply_markup=kb([("❌ Bekor qilish", "pair:cancel")]),
        )


@router.callback_query(F.data == "pair:cancel")
async def pair_cancel(cb: CallbackQuery):
    uid = cb.from_user.id
    if uid in pair_queue:
        pair_queue.remove(uid)
    await cb.answer("Bekor qilindi")
    await cb.message.edit_text(menu_text(), reply_markup=MENU_KB, parse_mode="HTML")


@router.callback_query(F.data == "pair:next")
async def pair_next(cb: CallbackQuery):
    uid = cb.from_user.id
    if uid not in partner:
        await cb.answer("Siz suhbatda emassiz", show_alert=True)
        return
    await cb.answer()
    q = random.choice(SPEAKING_QUESTIONS)
    for u in (uid, partner[uid]):
        await cb.bot.send_message(u, f"❓ <b>Yangi savol:</b>\n{q}", parse_mode="HTML")


@router.callback_query(F.data == "pair:leave")
async def pair_leave(cb: CallbackQuery):
    uid = cb.from_user.id
    await cb.answer()
    await leave_everything(cb.bot, uid)
    await send_menu(cb.bot, uid)


# ---------------- Group Discussion ----------------
@router.callback_query(F.data == "group:join")
async def group_join(cb: CallbackQuery):
    global group_timer
    uid = cb.from_user.id
    names[uid] = cb.from_user.first_name or "User"
    if busy(uid):
        await cb.answer("Avval joriy sessiyani tugating (/stop)", show_alert=True)
        return
    await cb.answer()
    group_queue.append(uid)

    if len(group_queue) >= MAX_GROUP:
        await start_room(cb.bot)
        return

    if len(group_queue) >= MIN_GROUP and (group_timer is None or group_timer.done()):
        group_timer = asyncio.create_task(delayed_start(cb.bot))

    await refresh_queue_messages(cb.bot, cb.message)


async def refresh_queue_messages(bot: Bot, message: Message):
    n = len(group_queue)
    extra = ""
    if n >= MIN_GROUP:
        extra = f"\n⏱ {GROUP_WAIT_SECONDS} soniyada boshlanadi (yoki {MAX_GROUP} kishi to'lsa)."
    try:
        await message.edit_text(
            f"⏳ Guruh yig'ilmoqda: <b>{n}/{MAX_GROUP}</b> (kamida {MIN_GROUP}){extra}",
            reply_markup=kb([("❌ Bekor qilish", "group:cancel")]),
            parse_mode="HTML",
        )
    except Exception:
        pass


async def delayed_start(bot: Bot):
    await asyncio.sleep(GROUP_WAIT_SECONDS)
    if len(group_queue) >= MIN_GROUP:
        await start_room(bot)


async def start_room(bot: Bot):
    members = group_queue[:MAX_GROUP]
    del group_queue[:MAX_GROUP]
    room = Room(id=next(_room_ids), topic=random.choice(DEBATE_TOPICS), members=members)
    rooms[room.id] = room
    for u in members:
        room_of[u] = room.id

    people = "\n".join(f"• {names.get(u, 'User')}" for u in members)
    text = (
        "🔥 <b>Debate boshlandi!</b>\n\n"
        f"📌 <b>Mavzu:</b>\n{room.topic}\n\n"
        f"👥 <b>Ishtirokchilar:</b>\n{people}\n\n"
        "Qoidalar: navbat bilan gapiring, hurmat bilan bahslashing, "
        "fikringizni sabab va misol bilan asoslang. Matn yoki voice yuborishingiz mumkin."
    )
    controls = kb(
        [("🔀 Yangi mavzu", "group:topic")],
        [("🚪 Chiqish", "group:leave")],
    )
    for u in members:
        await bot.send_message(u, text, reply_markup=controls, parse_mode="HTML")


@router.callback_query(F.data == "group:cancel")
async def group_cancel(cb: CallbackQuery):
    uid = cb.from_user.id
    if uid in group_queue:
        group_queue.remove(uid)
    await cb.answer("Bekor qilindi")
    await cb.message.edit_text(menu_text(), reply_markup=MENU_KB, parse_mode="HTML")


@router.callback_query(F.data == "group:topic")
async def group_topic(cb: CallbackQuery):
    uid = cb.from_user.id
    rid = room_of.get(uid)
    if rid is None:
        await cb.answer("Siz xonada emassiz", show_alert=True)
        return
    await cb.answer()
    room = rooms[rid]
    room.topic = random.choice([t for t in DEBATE_TOPICS if t != room.topic])
    for u in room.members:
        await cb.bot.send_message(
            u, f"🔀 <b>Yangi mavzu:</b>\n{room.topic}", parse_mode="HTML"
        )


@router.callback_query(F.data == "group:leave")
async def group_leave(cb: CallbackQuery):
    uid = cb.from_user.id
    await cb.answer()
    if uid in room_of:
        await leave_room(cb.bot, uid)
    await send_menu(cb.bot, uid)


async def leave_room(bot: Bot, uid: int):
    rid = room_of.pop(uid, None)
    if rid is None:
        return
    room = rooms.get(rid)
    if not room:
        return
    if uid in room.members:
        room.members.remove(uid)
    for u in room.members:
        await bot.send_message(u, f"🚪 {names.get(uid, 'User')} xonadan chiqdi.")
    if len(room.members) < 2:
        for u in list(room.members):
            room_of.pop(u, None)
            await bot.send_message(u, "Debate yakunlandi (ishtirokchilar yetarli emas).")
            await send_menu(bot, u)
        rooms.pop(rid, None)


# ---------------- Xabarlarni uzatish (relay) ----------------
@router.message()
async def relay(m: Message):
    uid = m.from_user.id

    # 1-on-1: anonim uzatish
    if uid in partner:
        await m.bot.copy_message(
            chat_id=partner[uid], from_chat_id=m.chat.id, message_id=m.message_id
        )
        return

    # Guruh: ism bilan uzatish
    rid = room_of.get(uid)
    if rid is not None:
        room = rooms[rid]
        name = names.get(uid, "User")
        for u in room.members:
            if u == uid:
                continue
            if m.text:
                await m.bot.send_message(u, f"👤 <b>{name}:</b> {m.html_text}", parse_mode="HTML")
            else:
                await m.bot.send_message(u, f"👤 <b>{name}</b> yubordi:", parse_mode="HTML")
                await m.bot.copy_message(
                    chat_id=u, from_chat_id=m.chat.id, message_id=m.message_id
                )
        return

    await m.answer("Menyuni ochish uchun /menu bosing.")


async def main():
    runner = web.AppRunner(make_app())
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", "8080")))
    await site.start()

    bot = Bot(TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
