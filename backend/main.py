import socketio
import uvicorn
import asyncio
import random
import logging
import os
from typing import List, Dict, Optional
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from game_logic import MafiaGame, Role, GameState, Team

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mafia_server")

app = FastAPI()

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 프론트엔드 정적 파일 경로 설정
FRONTEND_PATH = os.path.join(os.path.dirname(__file__), "../frontend")
IMAGE_PATH = os.path.join(FRONTEND_PATH, "image")
if not os.path.exists(IMAGE_PATH):
    os.makedirs(IMAGE_PATH, exist_ok=True)

app.mount("/image", StaticFiles(directory=IMAGE_PATH), name="image")

sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')
socket_app = socketio.ASGIApp(sio, app)

# 게임 인스턴스들을 관리하는 딕셔너리
rooms: Dict[str, MafiaGame] = {}
player_to_room: Dict[str, str] = {}

@app.get("/")
async def get_index():
    return FileResponse(os.path.join(FRONTEND_PATH, "index.html"))

@sio.event
async def connect(sid, environ):
    logger.info(f"Client connected: {sid}")

@sio.event
async def disconnect(sid):
    if sid in player_to_room:
        room_id = player_to_room[sid]
        if room_id in rooms:
            game = rooms[room_id]
            if sid in game.players:
                player_name = game.players[sid].name
                del game.players[sid]
                if not game.players:
                    if room_id in rooms: del rooms[room_id]
                else:
                    if game.host_id == sid:
                        game.host_id = list(game.players.keys())[0]
                    await broadcast_player_list(room_id)
                    await sio.emit("receive_chat", {"sender": "시스템", "message": f"[{player_name}]님이 방을 나갔습니다.", "type": "system"}, room=room_id)
        del player_to_room[sid]
    logger.info(f"Client disconnected: {sid}")

@sio.event
async def create_room(sid, data):
    room_id = f"room_{random.randint(1000, 9999)}"
    while room_id in rooms: room_id = f"room_{random.randint(1000, 9999)}"
    rooms[room_id] = MafiaGame(room_id)
    rooms[room_id].host_id = sid
    return {"room_id": room_id}

@sio.event
async def join_room(sid, data):
    room_id = data.get("room_id")
    player_name = data.get("name", f"Player_{sid[:4]}")
    if room_id in rooms:
        game = rooms[room_id]
        if game.state != GameState.WAITING:
            await sio.emit("error", {"message": "이미 게임이 시작된 방입니다."}, room=sid)
            return
        game.add_player(sid, player_name)
        player_to_room[sid] = room_id
        await sio.enter_room(sid, room_id)
        await sio.emit("room_joined", {"room_id": room_id, "host_id": game.host_id}, room=sid)
        await broadcast_player_list(room_id)
        await sio.emit("receive_chat", {"sender": "시스템", "message": f"[{player_name}]님이 입장했습니다.", "type": "system"}, room=room_id)
    else:
        await sio.emit("error", {"message": "존재하지 않는 방입니다."}, room=sid)

async def broadcast_player_list(room_id):
    if room_id in rooms:
        game = rooms[room_id]
        sorted_players = list(game.players.values())
        for sid in game.players.keys():
            player_data = []
            requester = game.players.get(sid)
            is_mafia_requester = requester and (requester.role == Role.MAFIA or (requester.role in [Role.SPY, Role.BEAST_MAN] and requester.is_contacted))
            for i, p in enumerate(sorted_players):
                p_role = p.revealed_role.value if p.revealed_role else None
                is_teammate = False
                if is_mafia_requester and (p.role == Role.MAFIA or (p.role in [Role.SPY, Role.BEAST_MAN] and p.is_contacted)):
                    p_role = p.role.value
                    is_teammate = True
                player_data.append({
                    "index": i + 1, "id": p.player_id, "name": p.name, "is_alive": p.is_alive,
                    "revealed_role": p_role, "status_msg": p.status_msg, "is_teammate": is_teammate
                })
            await sio.emit("player_list", player_data, room=sid)

@sio.event
async def start_game(sid, data):
    room_id = player_to_room.get(sid)
    if not room_id or room_id not in rooms: return
    game = rooms[room_id]
    if sid != game.host_id: return
    game.settings["start_state"] = data.get("start_state", "NIGHT")
    if game.start_game():
        mafia_team_ids = [p.player_id for p in game.players.values() if p.role == Role.MAFIA]
        lovers_names = [p.name for p in game.players.values() if p.role == Role.LOVERS]
        for pid, p in game.players.items():
            await sio.emit("game_started", {"role": p.role.value}, room=pid)
            if p.role == Role.MAFIA:
                m_list = ", ".join([game.players[m_id].name for m_id in mafia_team_ids])
                await sio.emit("receive_chat", {"sender": "시스템", "message": f"마피아 팀원: {m_list}", "type": "mafia"}, room=pid)
            if p.role == Role.LOVERS:
                partner = next((name for name in lovers_names if name != p.name), "알 수 없음")
                await sio.emit("receive_chat", {"sender": "시스템", "message": f"당신의 연인은 [{partner}]님입니다.", "type": "lovers"}, room=pid)
        game.timer = game.settings["night_duration"] if game.state == GameState.NIGHT else game.settings["day_duration"]
        await sio.emit("game_info", {"state": game.state.name, "day": game.day_count}, room=room_id)
        await broadcast_player_list(room_id)
    else:
        await sio.emit("error", {"message": "최소 4명의 플레이어가 필요합니다."}, room=sid)

@sio.event
async def skip_timer(sid):
    room_id = player_to_room.get(sid)
    if room_id in rooms:
        game = rooms[room_id]
        player = game.players.get(sid)
        if player and player.is_alive and not player.has_skipped:
            if game.timer > 10:
                game.timer -= 10
                player.has_skipped = True
                await sio.emit("timer", {"time": game.timer}, room=room_id)
                await sio.emit("receive_chat", {"sender": "시스템", "message": f"[{player.name}]님이 시간을 10초 단축시켰습니다.", "type": "system"}, room=room_id)

async def process_night_auto(room_id):
    if room_id not in rooms: return
    game = rooms[room_id]
    try:
        # 정보 판정 및 Unicast 처리
        for pid, p in game.players.items():
            if not p.is_alive or not p.target_id: continue
            target = game.players.get(p.target_id)
            if not target: continue
            
            if p.role == Role.POLICE:
                res = "마피아입니다." if target.role == Role.MAFIA else "마피아가 아닙니다."
                await sio.emit("receive_chat", {"sender": "수사", "message": f"[{target.name}]님은 {res}", "type": "system"}, room=pid)
            elif p.role == Role.SPY:
                await sio.emit("receive_chat", {"sender": "첩보", "message": f"조사하신 [{target.name}]님의 직업은 [{target.role.value}]입니다.", "type": "system"}, room=pid)
                if target.role == Role.SOLDIER:
                    await sio.emit("receive_chat", {"sender": "방첩", "message": f"[{p.name}]님이 당신을 조사했습니다!", "type": "system"}, room=target.player_id)
            elif p.role == Role.DETECTIVE:
                # 사립탐정 조사 로직 (손을 본다)
                hand_target_id = target.target_id
                if hand_target_id:
                    goal_p = game.players.get(hand_target_id)
                    goal_name = goal_p.name if goal_p else "알 수 없음"
                    msg = f"지난 밤 [{target.name}]님은 [{goal_name}]님에게 손을 댔습니다!"
                else:
                    msg = f"지난 밤 [{target.name}]님은 아무런 행동도 하지 않았습니다."
                await sio.emit("receive_chat", {"sender": "추리", "message": msg, "type": "system"}, room=pid)
            elif p.role == Role.MEDIUM and not target.is_alive:
                await sio.emit("receive_chat", {"sender": "성불", "message": f"성불시킨 [{target.name}]님의 직업은 [{target.role.value}]였습니다.", "type": "system"}, room=pid)

        for p in game.players.values():
            if p.is_alive and p.is_threatened:
                await sio.emit("receive_chat", {"sender": "협박", "message": "건달에게 협박을 당하여 투표를 할 수 없습니다!", "type": "system"}, room=p.player_id)

        for p in game.players.values():
            if p.is_alive and p.role == Role.SOLDIER and p.is_bulletproof_used:
                await sio.emit("receive_chat", {"sender": "군인", "message": "마피아의 공격을 버텨냈습니다!", "type": "system"}, room=p.player_id)

        game.process_night_actions()
        for log in game.logs[-5:]:
            await sio.emit("receive_chat", {"sender": "시스템", "message": log, "type": "system"}, room=room_id)
        
        await broadcast_player_list(room_id)
        winner = game.check_victory()
        if winner:
            all_roles = {p.player_id: p.role.value for p in game.players.values()}
            await sio.emit("game_over", {"winner": "마피아" if winner == Team.MAFIA else "시민", "roles": all_roles}, room=room_id)
            game.state = GameState.FINISHED
            # 10초 후 자동으로 대기실로 이동
            asyncio.create_task(auto_back_to_lobby(room_id))
        else:
            game.timer = 5 # MORNING
            await sio.emit("game_info", {"state": game.state.name, "day": game.day_count}, room=room_id)
    except Exception as e:
        logger.error(f"Error in process_night_auto: {e}")

async def auto_back_to_lobby(room_id):
    """게임 종료 후 일정 시간 뒤에 자동으로 대기실로 복귀"""
    await asyncio.sleep(10) # 10초 동안 결과 확인 시간 부여
    if room_id in rooms:
        game = rooms[room_id]
        if game.state == GameState.FINISHED:
            game.reset_game_state()
            await sio.emit("returned_to_lobby", room=room_id)
            await broadcast_player_list(room_id)
            await sio.emit("receive_chat", {"sender": "시스템", "message": "게임이 종료되어 대기실로 자동 이동되었습니다.", "type": "system"}, room=room_id)

async def process_voting_results(room_id):
    if room_id not in rooms: return
    game = rooms[room_id]
    tally = game.get_vote_results()
    if not tally:
        game.state = GameState.NIGHT
        game.day_count += 1
        game.timer = game.settings["night_duration"]
        await sio.emit("game_info", {"state": game.state.name, "day": game.day_count}, room=room_id)
        return
    max_v = max(tally.values())
    cand = [pid for pid, v in tally.items() if v == max_v]
    if len(cand) == 1 and max_v > 0:
        game.nominee_id = cand[0]
        game.state = GameState.LAST_ARGUMENT
        game.timer = 15
        await sio.emit("nominee_alert", {"name": game.players[game.nominee_id].name}, room=room_id)
    else:
        game.state = GameState.NIGHT
        game.day_count += 1
        game.timer = game.settings["night_duration"]
        await sio.emit("receive_chat", {"sender": "시스템", "message": "투표 결과가 동수이거나 투표가 없어 밤이 되었습니다.", "type": "system"}, room=room_id)
    game.reset_votes()
    await sio.emit("game_info", {"state": game.state.name, "day": game.day_count}, room=room_id)

async def process_judgement_results(room_id):
    if room_id not in rooms: return
    game = rooms[room_id]
    yes = sum([2 if p.role == Role.POLITICIAN else 1 for p in game.players.values() if p.is_judgement_yes is True])
    no = sum([2 if p.role == Role.POLITICIAN else 1 for p in game.players.values() if p.is_judgement_yes is False])
    if yes > no:
        nom = game.players[game.nominee_id]
        if nom.role == Role.POLITICIAN:
            await sio.emit("receive_chat", {"sender": "시스템", "message": f"[{nom.name}]님은 정치인이므로 투표로 죽일 수 없습니다.", "type": "system"}, room=room_id)
        else:
            game.kill_player(nom, "투표 처형", reveal=True)
            await sio.emit("receive_chat", {"sender": "시스템", "message": f"[{nom.name}]님이 처형되었습니다. 직업은 [{nom.role.value}]였습니다.", "type": "system"}, room=room_id)
            await broadcast_player_list(room_id)
    else:
        await sio.emit("receive_chat", {"sender": "시스템", "message": "찬성 표가 부족하여 처형되지 않았습니다.", "type": "system"}, room=room_id)
    winner = game.check_victory()
    if winner:
        all_roles = {p.player_id: p.role.value for p in game.players.values()}
        await sio.emit("game_over", {"winner": "마피아" if winner == Team.MAFIA else "시민", "roles": all_roles}, room=room_id)
        game.state = GameState.FINISHED
        # 10초 후 자동으로 대기실로 이동
        asyncio.create_task(auto_back_to_lobby(room_id))
    else:
        game.state = GameState.NIGHT
        game.day_count += 1
        game.timer = game.settings["night_duration"]
        await sio.emit("game_info", {"state": game.state.name, "day": game.day_count}, room=room_id)

@sio.event
async def update_memo(sid, data):
    room_id = player_to_room.get(sid)
    if room_id: rooms[room_id].players[sid].memos[data.get("target_id")] = data.get("memo")

@sio.event
async def vote(sid, data):
    room_id = player_to_room.get(sid)
    if not room_id: return
    game = rooms[room_id]
    p = game.players.get(sid)
    if game.state == GameState.VOTING and p and p.is_alive and not p.is_threatened:
        p.voted_for = data.get("target_id")
        await sio.emit("vote_tally", game.get_vote_results(), room=room_id)

@sio.event
async def judgement_vote(sid, data):
    room_id = player_to_room.get(sid)
    if room_id: rooms[room_id].players[sid].is_judgement_yes = data.get("is_yes")

@sio.event
async def back_to_lobby(sid):
    room_id = player_to_room.get(sid)
    if room_id and rooms[room_id].host_id == sid:
        rooms[room_id].reset_game_state()
        await sio.emit("returned_to_lobby", room=room_id)
        await broadcast_player_list(room_id)

@sio.event
async def night_action(sid, data):
    room_id = player_to_room.get(sid)
    if not room_id: return
    game = rooms[room_id]
    try:
        tid = data.get("target_id")
        p = game.players.get(sid)
        if p and p.is_alive and game.state == GameState.NIGHT:
            p.target_id = tid
            is_m = (p.role == Role.MAFIA or (p.role in [Role.SPY, Role.BEAST_MAN] and p.is_contacted))
            if is_m:
                if p.role == Role.MAFIA: game.mafia_target_id = tid
                m_ids = [x.player_id for x in game.players.values() if (x.role == Role.MAFIA or (x.role in [Role.SPY, Role.BEAST_MAN] and x.is_contacted)) and x.is_alive]
                for mid in m_ids:
                    if mid != sid: await sio.emit("mafia_target_sync", {"attacker_id": sid, "target_id": tid}, room=mid)
            if game.has_night_ability(p.role):
                t_name = game.players[tid].name if tid and tid in game.players else "아무도 아님"
                await sio.emit("receive_chat", {"sender": "시스템", "message": f"[{t_name}]님을 선택하였습니다.", "type": "system"}, room=sid)
    except Exception as e: logger.error(f"Error in night_action: {e}")

@sio.event
async def send_chat(sid, data):
    room_id = player_to_room.get(sid)
    if not room_id: return
    game = rooms[room_id]
    try:
        p = game.players.get(sid)
        if not p: return
        if game.state == GameState.LAST_ARGUMENT and sid != game.nominee_id: return
        msg = data.get("message")
        c_data = {"sender": p.name, "message": msg, "type": "normal"}
        if not p.is_alive:
            c_data["type"] = "dead"
            for pid, x in game.players.items():
                if not x.is_alive or x.role == Role.MEDIUM: await sio.emit("receive_chat", c_data, room=pid)
            return
        if game.state == GameState.NIGHT:
            if p.role == Role.MAFIA or (p.role in [Role.SPY, Role.BEAST_MAN] and p.is_contacted):
                c_data["type"] = "mafia"
                for pid, x in game.players.items():
                    if x.role == Role.MAFIA or (p.role in [Role.SPY, Role.BEAST_MAN] and x.is_contacted): await sio.emit("receive_chat", c_data, room=pid)
            elif p.role == Role.LOVERS:
                c_data["type"] = "lovers"
                for pid, x in game.players.items():
                    if x.role == Role.LOVERS: await sio.emit("receive_chat", c_data, room=pid)
            return
        await sio.emit("receive_chat", c_data, room=room_id)
    except Exception as e: logger.error(f"Error in send_chat: {e}")

async def game_loop():
    while True:
        try:
            for rid, g in list(rooms.items()):
                if g.state == GameState.WAITING or g.state == GameState.FINISHED: continue
                if g.timer <= 0:
                    g.reset_skips()
                    if g.state == GameState.NIGHT: await process_night_auto(rid)
                    elif g.state == GameState.MORNING:
                        g.state = GameState.DAY
                        g.timer = g.settings["day_duration"]
                        await sio.emit("game_info", {"state": g.state.name, "day": g.day_count}, room=rid)
                    elif g.state == GameState.DAY:
                        g.state = GameState.VOTING
                        g.timer = 15
                        await sio.emit("game_info", {"state": g.state.name, "day": g.day_count}, room=rid)
                    elif g.state == GameState.VOTING: await process_voting_results(rid)
                    elif g.state == GameState.LAST_ARGUMENT:
                        g.state = GameState.JUDGEMENT
                        g.timer = 5
                        await sio.emit("game_info", {"state": g.state.name, "day": g.day_count}, room=rid)
                    elif g.state == GameState.JUDGEMENT: await process_judgement_results(rid)
                else:
                    await sio.emit("timer", {"time": g.timer}, room=rid)
                    g.timer -= 1
            await asyncio.sleep(1)
        except Exception as e: logger.error(f"Error: {e}"); await asyncio.sleep(1)

@app.on_event("startup")
async def startup_event(): asyncio.create_task(game_loop())

if __name__ == "__main__": uvicorn.run(socket_app, host="0.0.0.0", port=8090)
