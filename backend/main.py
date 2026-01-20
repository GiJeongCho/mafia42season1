import socketio
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from game_logic import MafiaGame, Role, GameState, Team
import logging
import os

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

sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')
socket_app = socketio.ASGIApp(sio, app)

# 게임 인스턴스
game = MafiaGame()

@app.get("/")
async def get_index():
    return FileResponse(os.path.join(FRONTEND_PATH, "index.html"))

@sio.event
async def connect(sid, environ):
    logger.info(f"Client connected: {sid}")

@sio.event
async def disconnect(sid):
    logger.info(f"Client disconnected: {sid}")

@sio.event
async def join_game(sid, data):
    try:
        player_name = data.get("name", f"Player_{sid[:4]}")
        game.add_player(sid, player_name)
        await sio.emit("player_list", [{"id": p.player_id, "name": p.name} for p in game.players.values()])
        logger.info(f"Player {player_name} joined game.")
    except Exception as e:
        logger.error(f"Error in join_game: {e}")

@sio.event
async def start_game(sid):
    try:
        if game.start_game():
            # 시작 시 밤으로 변경 (직업 능력 사용을 위해)
            game.state = GameState.NIGHT
            
            for pid, p in game.players.items():
                # 각 플레이어에게 자신의 직업 전송
                await sio.emit("game_started", {"role": p.role.value}, room=pid)
            
            await sio.emit("game_info", {"state": game.state.name, "day": game.day_count})
            logger.info("Game started and moved to NIGHT.")
        else:
            await sio.emit("error", {"message": "최소 4명의 플레이어가 필요합니다."})
    except Exception as e:
        logger.error(f"Error in start_game: {e}")

import asyncio

# ... 기존 임포트 유지 ...

# 게임 타이머 설정을 위한 비동기 루프
async def game_loop():
    while True:
        try:
            if game.state == GameState.WAITING or game.state == GameState.FINISHED:
                await asyncio.sleep(1)
                continue

            # 밤 (25초)
            if game.state == GameState.NIGHT:
                game.timer = 25
                while game.timer > 0:
                    await sio.emit("timer", {"time": game.timer})
                    await asyncio.sleep(1)
                    game.timer -= 1
                await process_night_auto()

            # 아침/결과 발표 (5초)
            elif game.state == GameState.MORNING:
                game.timer = 5
                while game.timer > 0:
                    await sio.emit("timer", {"time": game.timer})
                    await asyncio.sleep(1)
                    game.timer -= 1
                game.state = GameState.DAY
                await sio.emit("game_info", {"state": game.state.name, "day": game.day_count})

            # 낮/토론 (참가자 수 * 15초)
            elif game.state == GameState.DAY:
                live_count = len([p for p in game.players.values() if p.is_alive])
                game.timer = live_count * 15
                while game.timer > 0 and game.state == GameState.DAY:
                    await sio.emit("timer", {"time": game.timer})
                    await asyncio.sleep(1)
                    game.timer -= 1
                if game.state == GameState.DAY:
                    game.state = GameState.VOTING
                    await sio.emit("game_info", {"state": game.state.name, "day": game.day_count})

            # 투표 (15초)
            elif game.state == GameState.VOTING:
                game.timer = 15
                while game.timer > 0 and game.state == GameState.VOTING:
                    await sio.emit("timer", {"time": game.timer})
                    # 마지막 5초 전까지는 실시간 투표 집계 공유
                    if game.timer > 5:
                        await sio.emit("vote_tally", game.get_vote_results())
                    await asyncio.sleep(1)
                    game.timer -= 1
                await process_voting_results()

            # 최후의 반론 (15초)
            elif game.state == GameState.LAST_ARGUMENT:
                game.timer = 15
                while game.timer > 0:
                    await sio.emit("timer", {"time": game.timer})
                    await asyncio.sleep(1)
                    game.timer -= 1
                game.state = GameState.JUDGEMENT
                await sio.emit("game_info", {"state": game.state.name, "day": game.day_count})

            # 찬반 투표 (5초)
            elif game.state == GameState.JUDGEMENT:
                game.timer = 5
                while game.timer > 0:
                    await sio.emit("timer", {"time": game.timer})
                    await asyncio.sleep(1)
                    game.timer -= 1
                await process_judgement_results()

        except Exception as e:
            logger.error(f"Error in game_loop: {e}")
            await asyncio.sleep(1)

async def process_night_auto():
    # 기존 process_night 로직을 자동 실행용으로 분리
    try:
        # 경찰/스파이 조사 결과 전송
        for pid, player in game.players.items():
            if not player.is_alive or not player.target_id: continue
            target = game.players.get(player.target_id)
            if not target: continue
            if player.role == Role.POLICE:
                result = "마피아입니다." if target.role == Role.MAFIA else "마피아가 아닙니다."
                await sio.emit("investigation_result", {"message": f"조사 결과: {target.name}님은 {result}"}, room=pid)
            elif player.role == Role.SPY:
                await sio.emit("investigation_result", {"message": f"조사 결과: {target.name}님의 직업은 {target.role.value}입니다."}, room=pid)

        game.process_night_actions()
        await sio.emit("morning_results", {"dead": game.dead_last_night, "logs": game.logs[-5:]})
        await sio.emit("player_list", [{"id": p.player_id, "name": p.name, "is_alive": p.is_alive} for p in game.players.values()])
        
        winner = game.check_victory()
        if winner:
            await sio.emit("game_over", {"winner": "마피아" if winner == Team.MAFIA else "시민"})
            game.state = GameState.FINISHED
        else:
            await sio.emit("game_info", {"state": game.state.name, "day": game.day_count})
    except Exception as e:
        logger.error(f"Error in process_night_auto: {e}")

async def process_voting_results():
    tally = game.get_vote_results()
    if not tally:
        game.state = GameState.NIGHT
        game.day_count += 1
        await sio.emit("game_info", {"state": game.state.name, "day": game.day_count})
        return

    # 최다 득표자 선별
    max_votes = max(tally.values())
    candidates = [pid for pid, votes in tally.items() if votes == max_votes]
    
    if len(candidates) == 1 and max_votes > 0:
        game.nominee_id = candidates[0]
        game.state = GameState.LAST_ARGUMENT
        await sio.emit("nominee_alert", {"name": game.players[game.nominee_id].name})
    else:
        # 동점이거나 투표가 없으면 바로 밤으로
        game.state = GameState.NIGHT
        game.day_count += 1
        await sio.emit("game_info", {"state": game.state.name, "day": game.day_count})
    
    game.reset_votes()
    await sio.emit("game_info", {"state": game.state.name, "day": game.day_count})

async def process_judgement_results():
    yes_votes = len([p for p in game.players.values() if p.is_judgement_yes is True])
    no_votes = len([p for p in game.players.values() if p.is_judgement_yes is False])
    
    # 건달에게 협박당한 경우 무조건 반대로 간주 (로직 보강 필요시 여기에 추가)
    
    if yes_votes >= no_votes and yes_votes > 0:
        nominee = game.players[game.nominee_id]
        # 정치인 패시브: 투표로 죽지 않음
        if nominee.role == Role.POLITICIAN:
            await sio.emit("system_message", {"message": f"{nominee.name}님은 정치인의 권력으로 처형되지 않았습니다!"})
        else:
            nominee.is_alive = False
            await sio.emit("system_message", {"message": f"{nominee.name}님이 처형되었습니다."})
            await sio.emit("player_list", [{"id": p.player_id, "name": p.name, "is_alive": p.is_alive} for p in game.players.values()])
    else:
        await sio.emit("system_message", {"message": "찬성 표가 부족하여 처형되지 않았습니다."})

    winner = game.check_victory()
    if winner:
        await sio.emit("game_over", {"winner": "마피아" if winner == Team.MAFIA else "시민"})
        game.state = GameState.FINISHED
    else:
        game.state = GameState.NIGHT
        game.day_count += 1
        await sio.emit("game_info", {"state": game.state.name, "day": game.day_count})

@sio.event
async def update_memo(sid, data):
    target_id = data.get("target_id")
    memo_text = data.get("memo")
    if sid in game.players and target_id in game.players:
        game.players[sid].memos[target_id] = memo_text
        await sio.emit("memo_updated", {"target_id": target_id, "memo": memo_text}, room=sid)

@sio.event
async def vote(sid, data):
    if game.state == GameState.VOTING:
        target_id = data.get("target_id")
        if sid in game.players and (target_id in game.players or target_id is None):
            game.players[sid].voted_for = target_id

@sio.event
async def judgement_vote(sid, data):
    if game.state == GameState.JUDGEMENT:
        is_yes = data.get("is_yes")
        if sid in game.players:
            game.players[sid].is_judgement_yes = is_yes

@sio.event
async def night_action(sid, data):
    try:
        target_id = data.get("target_id")
        player = game.players.get(sid)
        
        if player and player.is_alive and game.state == GameState.NIGHT:
            # 타겟 업데이트 (실시간 총구 공유용)
            player.target_id = target_id
            
            # 마피아 팀이면 같은 팀원들에게 타겟 정보 공유
            if player.role == Role.MAFIA:
                mafia_ids = [p.player_id for p in game.players.values() if p.role == Role.MAFIA and p.is_alive]
                contacted_ids = [p.player_id for p in game.players.values() if p.role in [Role.SPY, Role.BEAST_MAN] and p.is_contacted and p.is_alive]
                for mid in (mafia_ids + contacted_ids):
                    if mid != sid:
                        await sio.emit("mafia_target_sync", {"attacker_id": sid, "target_id": target_id}, room=mid)

            if game.has_night_ability(player.role):
                logger.info(f"Night action: {player.name} ({player.role.value}) targets {target_id}")
                await sio.emit("action_confirmed", {"message": "능력을 사용했습니다."}, room=sid)
                
                # 스파이가 군인을 조사한 경우 군인에게 알림
                if player.role == Role.SPY:
                    target = game.players.get(target_id)
                    if target and target.role == Role.SOLDIER:
                        await sio.emit("system_message", {"message": " 누군가 당신의 신분을 조사했습니다!"}, room=target_id)
            else:
                await sio.emit("error", {"message": "밤에 능력을 사용할 수 없는 직업입니다."}, room=sid)
    except Exception as e:
        logger.error(f"Error in night_action: {e}")

@sio.event
async def send_chat(sid, data):
    try:
        message = data.get("message")
        player = game.players.get(sid)
        if not player or not message:
            return

        # 최후의 반론 시간에는 해당 플레이어만 채팅 가능
        if game.state == GameState.LAST_ARGUMENT:
            if sid != game.nominee_id:
                return

        chat_data = {"sender": player.name, "message": message, "type": "normal"}

        # 사망자 채팅
        if not player.is_alive:
            chat_data["type"] = "dead"
            # 사망자와 영매에게만 전송
            for pid, p in game.players.items():
                if not p.is_alive or p.role == Role.MEDIUM:
                    await sio.emit("receive_chat", chat_data, room=pid)
            return

        # 밤 채팅 (마피아팀 / 연인)
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

        # 낮 채팅 (모두에게)
        await sio.emit("receive_chat", chat_data)
        
    except Exception as e:
        logger.error(f"Error in send_chat: {e}")

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(game_loop())

if __name__ == "__main__":
    uvicorn.run(socket_app, host="0.0.0.0", port=8001)

