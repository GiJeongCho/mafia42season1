import random
import logging
from enum import Enum, auto
from typing import List, Dict, Optional, Set

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mafia_game")

class Role(Enum):
    MAFIA = "마피아"
    SPY = "스파이"
    BEAST_MAN = "짐승인간"
    POLICE = "경찰"
    DOCTOR = "의사"
    SOLDIER = "군인"
    POLITICIAN = "정치인"
    GANGSTER = "건달"
    MEDIUM = "영매"
    REPORTER = "기자"
    DETECTIVE = "사립탐정"
    LOVERS = "연인"
    CITIZEN = "시민"

class Team(Enum):
    MAFIA = auto()
    CITIZEN = auto()

class GameState(Enum):
    WAITING = auto()
    DAY = auto()
    VOTING = auto()
    NIGHT = auto()
    MORNING = auto()
    FINISHED = auto()

class Player:
    def __init__(self, player_id: str, name: str):
        self.player_id = player_id
        self.name = name
        self.role: Optional[Role] = None
        self.is_alive = True
        self.is_contacted = False  # 스파이/짐인 접선 여부
        self.is_threatened = False  # 건달에게 협박당함
        self.is_protected = False  # 의사 치료
        self.is_bulletproof_used = False # 군인 방탄 사용 여부
        self.votes = 0
        self.target_id: Optional[str] = None # 밤에 선택한 대상
        self.voted_for: Optional[str] = None # 낮에 투표한 대상

    def __repr__(self):
        return f"Player({self.name}, {self.role}, Alive: {self.is_alive})"

class MafiaGame:
    def __init__(self):
        self.players: Dict[str, Player] = {}
        self.state = GameState.WAITING
        self.day_count = 1
        self.dead_last_night: List[str] = []
        self.logs: List[str] = []
        self.reporter_used = False

    def add_player(self, player_id: str, name: str):
        if player_id not in self.players:
            self.players[player_id] = Player(player_id, name)
            logger.info(f"Player added: {name}")

    def assign_roles(self):
        # 테스트용으로 간단히 배정 (실제로는 인원수에 맞게 조정 필요)
        player_ids = list(self.players.keys())
        random.shuffle(player_ids)
        
        roles_to_assign = [
            Role.MAFIA, Role.POLICE, Role.DOCTOR, Role.SPY, 
            Role.BEAST_MAN, Role.SOLDIER, Role.POLITICIAN, Role.GANGSTER
        ]
        
        for i, pid in enumerate(player_ids):
            if i < len(roles_to_assign):
                self.players[pid].role = roles_to_assign[i]
            else:
                self.players[pid].role = Role.CITIZEN
        
        logger.info("Roles assigned.")

    def start_game(self):
        if len(self.players) < 4:
            return False
        self.assign_roles()
        self.state = GameState.DAY
        return True

    def process_night_actions(self):
        """밤 사이클 로직 처리 (우선순위 고려)"""
        try:
            mafia_target = None
            doctor_target = None
            actions = {p.role: p.target_id for p in self.players.values() if p.is_alive and p.target_id}
            
            # 1. 건달 협박 (가장 먼저 적용)
            for pid, player in self.players.items():
                if player.role == Role.GANGSTER and player.is_alive and player.target_id:
                    target = self.players.get(player.target_id)
                    if target:
                        target.is_threatened = True
                        self.logs.append(f"건달이 누군가를 협박했습니다.")

            # 2. 의사 치료 및 마피아 공격
            for pid, player in self.players.items():
                if player.role == Role.DOCTOR and player.is_alive and player.target_id:
                    doctor_target = player.target_id
                if player.role == Role.MAFIA and player.is_alive and player.target_id:
                    mafia_target = player.target_id

            # 3. 마피아 공격 결과 판정
            self.dead_last_night = []
            if mafia_target:
                target_player = self.players.get(mafia_target)
                if target_player:
                    if mafia_target == doctor_target:
                        self.logs.append(f"의사가 마피아의 공격을 막아냈습니다.")
                    elif target_player.role == Role.SOLDIER and not target_player.is_bulletproof_used:
                        target_player.is_bulletproof_used = True
                        self.logs.append(f"군인이 방탄으로 살아남았습니다.")
                        # 군인은 스파이 조사 시 반사 능력도 있으나 여기서는 단순화
                    elif target_player.role == Role.BEAST_MAN:
                        # 짐승인간은 마피아 공격에 죽지 않음
                        self.logs.append(f"마피아가 짐승인간을 공격했으나 효과가 없었습니다.")
                    else:
                        target_player.is_alive = False
                        self.dead_last_night.append(target_player.player_id)
                        self.logs.append(f"{target_player.name}님이 사망하셨습니다.")

            # 4. 조사 계열 (경찰, 스파이, 사립탐정 등)
            for pid, player in self.players.items():
                if not player.is_alive or not player.target_id: continue
                
                target = self.players.get(player.target_id)
                if not target: continue

                if player.role == Role.POLICE:
                    res = "마피아입니다." if target.role == Role.MAFIA else "마피아가 아닙니다."
                    # TODO: 조사 결과 전달 로직
                elif player.role == Role.SPY:
                    if target.role == Role.MAFIA:
                        player.is_contacted = True
                    # TODO: 직업 결과 전달 로직
                elif player.role == Role.BEAST_MAN:
                    if target.role == Role.MAFIA:
                        player.is_contacted = True

            # 상태 초기화
            for player in self.players.values():
                player.target_id = None
                player.is_protected = False

            self.state = GameState.MORNING
            
        except Exception as e:
            logger.error(f"Error processing night actions: {e}")
            raise

    def check_victory(self) -> Optional[Team]:
        mafias = [p for p in self.players.values() if p.is_alive and p.role in [Role.MAFIA, Role.SPY, Role.BEAST_MAN]]
        citizens = [p for p in self.players.values() if p.is_alive and p.role not in [Role.MAFIA, Role.SPY, Role.BEAST_MAN]]
        
        # 실제 마피아(공격권 가진)가 죽었을 때 짐승인간이 활동하는 등의 세부 규칙은 추후 보강
        live_mafias = [p for p in self.players.values() if p.is_alive and p.role == Role.MAFIA]
        
        if not live_mafias and not [p for p in mafias if p.role == Role.BEAST_MAN and p.is_contacted]:
            return Team.CITIZEN
        
        if len(mafias) >= len(citizens):
            return Team.MAFIA
            
        return None

    def reset_for_day(self):
        for player in self.players.values():
            player.is_threatened = False
            player.votes = 0
            player.voted_for = None

