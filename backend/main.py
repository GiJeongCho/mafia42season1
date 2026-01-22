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
                logger.info(f"Player {player_name} removed from room {room_id}")
                
                if not game.players:
                    if room_id in rooms:
                        del rooms[room_id]
                    logger.info(f"Room {room_id} deleted (empty)")
                else:
                    # 방장이 나갔으면 권한 위임
                    if game.host_id == sid:
                        game.host_id = list(game.players.keys())[0]
                        logger.info(f"Host migrated to {game.players[game.host_id].name}")
                    
                    await broadcast_player_list(room_id)
                    await sio.emit("receive_chat", {"sender": "시스템", "message": f"[{player_name}]님이 방을 나갔습니다.", "type": "normal"}, room=room_id)
        del player_to_room[sid]
    logger.info(f"Client disconnected: {sid}")

@sio.event
async def create_room(sid, data):
    room_id = f"room_{random.randint(1000, 9999)}"
    while room_id in rooms:
        room_id = f"room_{random.randint(1000, 9999)}"
    rooms[room_id] = MafiaGame(room_id)
    rooms[room_id].host_id = sid
    logger.info(f"Room created: {room_id} by {sid}")
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
        
        # 정보 전송 순서: 가입 승인 -> 목록 업데이트 -> 입장 메시지
        await sio.emit("room_joined", {"room_id": room_id, "host_id": game.host_id}, room=sid)
        await broadcast_player_list(room_id)
        await sio.emit("receive_chat", {"sender": "시스템", "message": f"[{player_name}]님이 입장했습니다.", "type": "normal"}, room=room_id)
        logger.info(f"Player {player_name} joined room {room_id}")
    else:
        await sio.emit("error", {"message": "존재하지 않는 방입니다."}, room=sid)

async def broadcast_player_list(room_id):
    if room_id in rooms:
        game = rooms[room_id]
        sorted_players = list(game.players.values())
        
        for sid in game.players.keys():
            player_data = []
            requester = game.players.get(sid)
            # 요청자가 마피아 팀인지 확인 (마피아거나 접선된 보조직업)
            is_mafia_requester = requester and (requester.role == Role.MAFIA or (requester.role in [Role.SPY, Role.BEAST_MAN] and requester.is_contacted))

            for i, p in enumerate(sorted_players):
                # 기본적으로 공개된 직업만 보여줌
                p_role_to_show = p.revealed_role.value if p.revealed_role else None
                is_teammate = False
                
                # 요청자가 마피아 팀이면 같은 팀원들의 정보를 볼 수 있음
                if is_mafia_requester:
                    if p.role == Role.MAFIA or (p.role in [Role.SPY, Role.BEAST_MAN] and p.is_contacted):
                        p_role_to_show = p.role.value
                        is_teammate = True

                player_data.append({
                    "index": i + 1,
                    "id": p.player_id, 
                    "name": p.name, 
                    "is_alive": p.is_alive,
                    "revealed_role": p_role_to_show,
                    "status_msg": p.status_msg,
                    "is_teammate": is_teammate
                })
            await sio.emit("player_list", player_data, room=sid)

@sio.event
async def start_game(sid, data):
    room_id = player_to_room.get(sid)
    if not room_id or room_id not in rooms: return
    
    game = rooms[room_id]
    if sid != game.host_id:
        await sio.emit("error", {"message": "방장만 게임을 시작할 수 있습니다."}, room=sid)
        return

    game.settings["start_state"] = data.get("start_state", "NIGHT")
    
    if game.start_game():
        mafia_team_ids = [p.player_id for p in game.players.values() if p.role == Role.MAFIA]
        lovers_names = [p.name for p in game.players.values() if p.role == Role.LOVERS]
        
        for pid, p in game.players.items():
            await sio.emit("game_started", {"role": p.role.value}, room=pid)
            if p.role == Role.MAFIA:
                mafia_list = ", ".join([game.players[m_id].name for m_id in mafia_team_ids])
                await sio.emit("receive_chat", {"sender": "시스템", "message": f"마피아 팀원: {mafia_list}", "type": "mafia"}, room=pid)
            if p.role == Role.LOVERS:
                partner_name = next((name for name in lovers_names if name != p.name), "알 수 없음")
                await sio.emit("receive_chat", {"sender": "시스템", "message": f"당신의 연인은 [{partner_name}]님입니다.", "type": "lovers"}, room=pid)

        game.timer = game.settings["night_duration"] if game.state == GameState.NIGHT else game.settings["day_duration"]
        await sio.emit("game_info", {"state": game.state.name, "day": game.day_count}, room=room_id)
        await broadcast_player_list(room_id)
        logger.info(f"Game started in room {room_id}")
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
                await sio.emit("receive_chat", {"sender": "시스템", "message": f"[{player.name}]님이 시간을 10초 단축시켰습니다.", "type": "normal"}, room=room_id)
            else:
                await sio.emit("error", {"message": "시간이 얼마 남지 않았습니다."}, room=sid)
        elif player and player.has_skipped:
            await sio.emit("error", {"message": "이미 스킵을 사용하셨습니다."}, room=sid)

async def process_night_auto(room_id):
    if room_id not in rooms: return
    game = rooms[room_id]
    try:
        # 경찰/스파이 조사 결과 전송
        for pid, player in game.players.items():
            if not player.is_alive or not player.target_id: continue
            target = game.players.get(player.target_id)
            if not target: continue
            if player.role == Role.POLICE:
                res = "마피아입니다." if target.role == Role.MAFIA else "마피아가 아닙니다."
                await sio.emit("investigation_result", {"message": f"조사 결과: {target.name}님은 {res}"}, room=pid)
            elif player.role == Role.SPY:
                await sio.emit("investigation_result", {"message": f"조사 결과: {target.name}님의 직업은 {target.role.value}입니다."}, room=pid)

        game.process_night_actions()
        game.timer = 5 # MORNING 지속 시간
        await sio.emit("morning_results", {"dead": game.dead_last_night, "logs": game.logs[-5:]}, room=room_id)
        await broadcast_player_list(room_id)
        
        winner = game.check_victory()
        if winner:
            await sio.emit("game_over", {"winner": "마피아" if winner == Team.MAFIA else "시민"}, room=room_id)
            game.state = GameState.FINISHED
        else:
            await sio.emit("game_info", {"state": game.state.name, "day": game.day_count}, room=room_id)
    except Exception as e:
        logger.error(f"Error in process_night_auto: {e}")

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

    max_votes = max(tally.values())
    candidates = [pid for pid, votes in tally.items() if votes == max_votes]
    
    if len(candidates) == 1 and max_votes > 0:
        game.nominee_id = candidates[0]
        game.state = GameState.LAST_ARGUMENT
        game.timer = 15
        await sio.emit("nominee_alert", {"name": game.players[game.nominee_id].name}, room=room_id)
    else:
        game.state = GameState.NIGHT
        game.day_count += 1
        game.timer = game.settings["night_duration"]
        await sio.emit("game_info", {"state": game.state.name, "day": game.day_count}, room=room_id)
    
    game.reset_votes()
    await sio.emit("game_info", {"state": game.state.name, "day": game.day_count}, room=room_id)

async def process_judgement_results(room_id):
    if room_id not in rooms: return
    game = rooms[room_id]
    
    yes_votes = 0
    no_votes = 0
    for p in game.players.values():
        if p.is_judgement_yes is not None:
            weight = 2 if p.role == Role.POLITICIAN else 1
            if p.is_judgement_yes: yes_votes += weight
            else: no_votes += weight
    
    if yes_votes > no_votes:
        nominee = game.players[game.nominee_id]
        if nominee.role == Role.POLITICIAN:
            await sio.emit("system_message", {"message": f"{nominee.name}님은 정치인이므로 투표로 죽일 수 없습니다!"}, room=room_id)
        else:
            game.kill_player(nominee, "투표 처형", reveal=True)
            await sio.emit("system_message", {"message": f"{nominee.name}님이 처형되었습니다. 직업은 [{nominee.role.value}]였습니다."}, room=room_id)
            await broadcast_player_list(room_id)
    else:
        await sio.emit("system_message", {"message": "찬성 표가 부족하여 처형되지 않았습니다."}, room=room_id)

    winner = game.check_victory()
    if winner:
        await sio.emit("game_over", {"winner": "마피아" if winner == Team.MAFIA else "시민"}, room=room_id)
        game.state = GameState.FINISHED
    else:
        game.state = GameState.NIGHT
        game.day_count += 1
        game.timer = game.settings["night_duration"]
        await sio.emit("game_info", {"state": game.state.name, "day": game.day_count}, room=room_id)

@sio.event
async def update_memo(sid, data):
    room_id = player_to_room.get(sid)
    if not room_id: return
    game = rooms[room_id]
    target_id = data.get("target_id")
    memo_text = data.get("memo")
    if sid in game.players and target_id in game.players:
        game.players[sid].memos[target_id] = memo_text
        await sio.emit("memo_updated", {"target_id": target_id, "memo": memo_text}, room=sid)

@sio.event
async def vote(sid, data):
    room_id = player_to_room.get(sid)
    if not room_id: return
    game = rooms[room_id]
    if game.state == GameState.VOTING:
        player = game.players.get(sid)
        if player and player.is_alive and not player.is_threatened:
            player.voted_for = data.get("target_id")
            await sio.emit("vote_tally", game.get_vote_results(), room=room_id)
            logger.info(f"Player {player.name} voted for {player.voted_for} in room {room_id}")

@sio.event
async def judgement_vote(sid, data):
    room_id = player_to_room.get(sid)
    if not room_id: return
    game = rooms[room_id]
    if game.state == GameState.JUDGEMENT:
        game.players[sid].is_judgement_yes = data.get("is_yes")

@sio.event
async def night_action(sid, data):
    room_id = player_to_room.get(sid)
    if not room_id: return
    game = rooms[room_id]
    try:
        target_id = data.get("target_id")
        player = game.players.get(sid)
        target = game.players.get(target_id)
        
        if player and player.is_alive and game.state == GameState.NIGHT:
            player.target_id = target_id
            
            # 1. 마피아 팀 실시간 공유 및 공동 타겟 업데이트
            is_mafia_team = (player.role == Role.MAFIA or (player.role in [Role.SPY, Role.BEAST_MAN] and player.is_contacted))
            if is_mafia_team:
                if player.role == Role.MAFIA:
                    game.mafia_target_id = target_id # 마지막에 클릭한 마피아의 타겟이 최종 타겟
                
                mafia_team_ids = [p.player_id for p in game.players.values() if (p.role == Role.MAFIA or (p.role in [Role.SPY, Role.BEAST_MAN] and p.is_contacted)) and p.is_alive]
                for mid in mafia_team_ids:
                    if mid != sid:
                        await sio.emit("mafia_target_sync", {"attacker_id": sid, "target_id": target_id}, room=mid)
            
            # 2. 사립탐정 실시간 추적 (타겟의 행적이 바뀌면 즉시 알림)
            for pid, p in game.players.items():
                if p.role == Role.DETECTIVE and p.is_alive and p.target_id == sid:
                    msg = f"당신이 지켜보는 {player.name}님이 [{target.name if target else '아무도 아님'}]님에게 손을 대고 있습니다."
                    await sio.emit("receive_chat", {"sender": "추리(실시간)", "message": msg, "type": "normal"}, room=pid)

            if game.has_night_ability(player.role):
                target_name = target.name if target else "알 수 없음"
                await sio.emit("action_confirmed", {"message": f"[{target_name}]님을 선택하였습니다.", "target_name": target_name}, room=sid)
    except Exception as e:
        logger.error(f"Error in night_action: {e}")

@sio.event
async def send_chat(sid, data):
    room_id = player_to_room.get(sid)
    if not room_id: return
    game = rooms[room_id]
    try:
        player = game.players.get(sid)
        if not player: return
        if game.state == GameState.LAST_ARGUMENT and sid != game.nominee_id: return
        
        message = data.get("message")
        chat_data = {"sender": player.name, "message": message, "type": "normal"}
        
        if not player.is_alive:
            chat_data["type"] = "dead"
            for pid, p in game.players.items():
                if not p.is_alive or p.role == Role.MEDIUM:
                    await sio.emit("receive_chat", chat_data, room=pid)
            return
        
        if game.state == GameState.NIGHT:
            if player.role == Role.MAFIA or (player.role in [Role.SPY, Role.BEAST_MAN] and player.is_contacted):
                chat_data["type"] = "mafia"
                for pid, p in game.players.items():
                    if p.role == Role.MAFIA or (p.role in [Role.SPY, Role.BEAST_MAN] and p.is_contacted):
                        await sio.emit("receive_chat", chat_data, room=pid)
            elif player.role == Role.LOVERS:
                chat_data["type"] = "lovers"
                for pid, p in game.players.items():
                    if p.role == Role.LOVERS:
                        await sio.emit("receive_chat", chat_data, room=pid)
            return
        await sio.emit("receive_chat", chat_data, room=room_id)
    except Exception as e:
        logger.error(f"Error in send_chat: {e}")

async def game_loop():
    while True:
        try:
            for room_id, game in list(rooms.items()):
                if game.state == GameState.WAITING or game.state == GameState.FINISHED: continue
                if game.timer <= 0:
                    game.reset_skips()
                    if game.state == GameState.NIGHT: await process_night_auto(room_id)
                    elif game.state == GameState.MORNING:
                        game.state = GameState.DAY
                        game.timer = game.settings["day_duration"]
                        await sio.emit("game_info", {"state": game.state.name, "day": game.day_count}, room=room_id)
                    elif game.state == GameState.DAY:
                        game.state = GameState.VOTING
                        game.timer = 15
                        await sio.emit("game_info", {"state": game.state.name, "day": game.day_count}, room=room_id)
                    elif game.state == GameState.VOTING: await process_voting_results(room_id)
                    elif game.state == GameState.LAST_ARGUMENT:
                        game.state = GameState.JUDGEMENT
                        game.timer = 5
                        await sio.emit("game_info", {"state": game.state.name, "day": game.day_count}, room=room_id)
                    elif game.state == GameState.JUDGEMENT: await process_judgement_results(room_id)
                else:
                    await sio.emit("timer", {"time": game.timer}, room=room_id)
                    if game.state == GameState.VOTING and game.timer > 5:
                        await sio.emit("vote_tally", game.get_vote_results(), room=room_id)
                    game.timer -= 1
            await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"Error in game_loop: {e}")
            await asyncio.sleep(1)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(game_loop())

if __name__ == "__main__":
    uvicorn.run(socket_app, host="0.0.0.0", port=8090)
