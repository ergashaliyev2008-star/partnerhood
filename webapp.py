import itertools
import random
from pathlib import Path

from aiohttp import WSMsgType, web

QUESTIONS = [
    "Tell me about your hometown.",
    "Do you prefer studying in the morning or evening? Why?",
    "Describe a person who has influenced you.",
    "Describe a place you would like to visit.",
    "How has technology changed the way people learn?",
    "Why do some people prefer to live abroad?",
]
TOPICS = [
    "Online learning is better than classroom learning.",
    "Private health care should be replaced by free public health care.",
    "Social media does more harm than good to teenagers.",
    "University education should be free for everyone.",
    "People nowadays tend to have children at older ages. Do the advantages outweigh the disadvantages?",
]
GROUP_MAX = 6

_ids = itertools.count(1)
waiting = None          # 1-on-1 kutayotgan
peers: dict = {}        # 1-on-1 juftlar
info: dict = {}         # ws -> {"id", "room"}
rooms: dict = {}        # room_id -> {"topic", "members": {id: ws}}
_room_ids = itertools.count(1)
open_room = None


async def safe_send(ws, data):
    if ws is not None and not ws.closed:
        await ws.send_json(data)


async def cleanup(ws):
    global waiting, open_room
    if waiting is ws:
        waiting = None
    other = peers.pop(ws, None)
    if other is not None:
        peers.pop(other, None)
        await safe_send(other, {"type": "peer_left"})
    me = info.pop(ws, None)
    if me and me.get("room") in rooms:
        room = rooms[me["room"]]
        room["members"].pop(me["id"], None)
        if not room["members"]:
            rooms.pop(me["room"], None)
            if open_room == me["room"]:
                open_room = None
        else:
            for w in room["members"].values():
                await safe_send(w, {"type": "peer_left", "id": me["id"], "count": len(room["members"])})


async def join_group(ws):
    global open_room
    if open_room not in rooms or len(rooms[open_room]["members"]) >= GROUP_MAX:
        open_room = next(_room_ids)
        rooms[open_room] = {"topic": random.choice(TOPICS), "members": {}}
    room = rooms[open_room]
    me = str(next(_ids))
    existing = list(room["members"].keys())
    room["members"][me] = ws
    info[ws] = {"id": me, "room": open_room}
    await ws.send_json({"type": "group_joined", "id": me, "peers": existing,
                        "topic": room["topic"], "count": len(room["members"])})
    for pid in existing:
        await safe_send(room["members"][pid],
                        {"type": "peer_joined", "id": me, "count": len(room["members"])})


async def ws_handler(request):
    global waiting
    ws = web.WebSocketResponse(heartbeat=25)
    await ws.prepare(request)
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            data = msg.json()
            t = data.get("type")
            if t == "join":
                if ws in peers or ws in info or waiting is ws:
                    continue
                if data.get("mode") == "group":
                    await join_group(ws)
                elif waiting is not None and not waiting.closed:
                    other, waiting = waiting, None
                    peers[ws], peers[other] = other, ws
                    q = random.choice(QUESTIONS)
                    await other.send_json({"type": "matched", "role": "caller", "question": q})
                    await ws.send_json({"type": "matched", "role": "callee", "question": q})
                else:
                    waiting = ws
                    await ws.send_json({"type": "waiting"})
            elif t == "signal":
                me = info.get(ws)
                if me and data.get("to"):
                    target = rooms.get(me["room"], {}).get("members", {}).get(data["to"])
                    await safe_send(target, {"type": "signal", "from": me["id"], "data": data["data"]})
                else:
                    await safe_send(peers.get(ws), {"type": "signal", "data": data["data"]})
            elif t == "leave":
                await cleanup(ws)
    finally:
        await cleanup(ws)
    return ws


async def index(request):
    return web.FileResponse(Path(__file__).parent / "index.html")


def make_app():
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/ws", ws_handler)
    return app
