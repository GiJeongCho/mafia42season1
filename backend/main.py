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

@sio.event
async def night_action(sid, data):
    try:
        target_id = data.get("target_id")
        if sid in game.players:
            game.players[sid].target_id = target_id
            logger.info(f"Night action received from {sid} for target {target_id}")
            
            # 모든 살아있는 플레이어가 행동을 완료했는지 체크 (간단화)
            # 여기서는 수동으로 밤을 끝내는 버튼이 있다고 가정하거나 타이머를 둘 수 있음
            pass
    except Exception as e:
        logger.error(f"Error in night_action: {e}")

@sio.event
async def process_night(sid):
    # 방장이 밤 행동 결과 처리 요청
    try:
        game.process_night_actions()
        await sio.emit("morning_results", {
            "dead": game.dead_last_night,
            "logs": game.logs[-5:] # 최근 로그 5개
        })
        
        winner = game.check_victory()
        if winner:
            await sio.emit("game_over", {"winner": winner.name})
            game.state = GameState.FINISHED
    except Exception as e:
        logger.error(f"Error in process_night: {e}")

if __name__ == "__main__":
    uvicorn.run(socket_app, host="0.0.0.0", port=8000)

