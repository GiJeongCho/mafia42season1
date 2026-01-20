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
    NIGHT = auto()
    MORNING = auto() # 결과 발표
    DAY = auto() # 토론
    VOTING = auto() # 투표
    LAST_ARGUMENT = auto() # 최후의 반론
    JUDGEMENT = auto() # 찬반 투표
    FINISHED = auto()

class Player:
    def __init__(self, player_id: str, name: str):
        self.player_id = player_id
        self.name = name
        self.role: Optional[Role] = None
        self.is_alive = True
        self.is_contacted = False  
        self.is_threatened = False  
        self.is_protected = False  
        self.is_bulletproof_used = False 
        self.votes = 0
        self.target_id: Optional[str] = None 
        self.voted_for: Optional[str] = None 
        self.memos: Dict[str, str] = {} # target_id: memo_text
        self.is_judgement_yes: Optional[bool] = None # 찬반 투표 결과

class MafiaGame:
    def __init__(self, room_id: str):
        self.room_id = room_id
        self.players: Dict[str, Player] = {}
        self.state = GameState.WAITING
        self.day_count = 1
        self.dead_last_night: List[str] = []
        self.logs: List[str] = []
        self.reporter_used = False
        self.timer = 0
        self.nominee_id: Optional[str] = None 
        self.host_id: Optional[str] = None
        self.settings = {
            "start_state": "NIGHT",
            "night_duration": 30,
            "day_duration": 90
        }

    def add_player(self, player_id: str, name: str):
        if player_id not in self.players:
            player = Player(player_id, name)
            self.players[player_id] = player
            if not self.host_id:
                self.host_id = player_id
            logger.info(f"Player added to room {self.room_id}: {name}")

    def start_game(self):
        if len(self.players) < 4:
            return False
        self.assign_roles()
        # 설정된 시작 상태에 따라 초기 상태 결정
        start_state = self.settings.get("start_state", "NIGHT")
        self.state = GameState.NIGHT if start_state == "NIGHT" else GameState.DAY
        return True

    def assign_roles(self):
        player_ids = list(self.players.keys())
        num_players = len(player_ids)
        random.shuffle(player_ids)
        
        # 마피아 팀 배정
        mafia_team_size = max(1, num_players // 4)
        mafia_team_roles = [Role.MAFIA] * mafia_team_size
        
        if num_players >= 5:
            mafia_team_roles.append(random.choice([Role.SPY, Role.BEAST_MAN]))
        
        citizen_roles_pool = [
            Role.POLICE, Role.DOCTOR, Role.SOLDIER, Role.POLITICIAN, 
            Role.GANGSTER, Role.MEDIUM, Role.REPORTER, Role.DETECTIVE, Role.LOVERS
        ]
        
        assigned_roles = mafia_team_roles
        remaining_count = num_players - len(assigned_roles)
        
        available_citizen_roles = citizen_roles_pool.copy()
        random.shuffle(available_citizen_roles)
        
        for i in range(remaining_count):
            if available_citizen_roles:
                assigned_roles.append(available_citizen_roles.pop())
            else:
                assigned_roles.append(random.choice(citizen_roles_pool))

        random.shuffle(assigned_roles)
        
        for i, pid in enumerate(player_ids):
            self.players[pid].role = assigned_roles[i]
            self.players[pid].is_alive = True # 명시적으로 생존 상태 초기화
            self.players[pid].memos = {other_pid: "" for other_pid in player_ids}

    def process_night_actions(self):
        """밤 사이클 로직 처리"""
        try:
            mafia_target = None
            doctor_target = None
            beastman_target = None
            
            # 살아있는 마피아 목록
            live_mafias = [p for p in self.players.values() if p.is_alive and p.role == Role.MAFIA]
            
            # 1. 행동 타겟 수집
            for player in self.players.values():
                if not player.is_alive or not player.target_id: continue
                
                if player.role == Role.MAFIA:
                    mafia_target = player.target_id
                elif player.role == Role.DOCTOR:
                    doctor_target = player.target_id
                elif player.role == Role.BEAST_MAN:
                    beastman_target = player.target_id
                elif player.role == Role.GANGSTER:
                    target = self.players.get(player.target_id)
                    if target: target.is_threatened = True

            self.dead_last_night = []
            
            # 2. 짐승인간 접선 및 공격 특수 로직
            bm_player = next((p for p in self.players.values() if p.role == Role.BEAST_MAN and p.is_alive), None)
            if bm_player and not bm_player.is_contacted:
                # 마피아가 짐승인간을 쏜 경우 접선
                if mafia_target == bm_player.player_id:
                    bm_player.is_contacted = True
                    self.logs.append("짐승인간이 마피아의 총격에서 살아남아 접선했습니다.")
                    mafia_target = None # 죽지 않음
                # 마피아와 짐승인간이 같은 대상을 쏜 경우 접선 및 습격 (의사 힐 무시)
                elif mafia_target and mafia_target == beastman_target:
                    bm_player.is_contacted = True
                    target_player = self.players.get(mafia_target)
                    if target_player:
                        target_player.is_alive = False
                        self.dead_last_night.append(target_player.name)
                        self.logs.append(f"짐승인간이 {target_player.name}님을 습격하여 접선에 성공했습니다!")
                    mafia_target = None # 이미 죽였으므로 마피아 타겟 무효화

            # 3. 일반 마피아 공격 판정
            if mafia_target:
                target_player = self.players.get(mafia_target)
                if target_player:
                    if mafia_target == doctor_target:
                        self.logs.append(f"의사가 누군가를 살려냈습니다.")
                    elif target_player.role == Role.SOLDIER and not target_player.is_bulletproof_used:
                        target_player.is_bulletproof_used = True
                        self.logs.append(f"군인이 습격을 버텨냈습니다.")
                    elif target_player.role == Role.BEAST_MAN:
                        self.logs.append(f"누군가 습격을 받았으나 멀쩡합니다.")
                    else:
                        target_player.is_alive = False
                        self.dead_last_night.append(target_player.name)
                        self.logs.append(f"{target_player.name}님이 사망하셨습니다.")
            
            # 4. 마피아 전멸 시 접선된 짐승인간의 공격
            if not live_mafias and bm_player and bm_player.is_contacted and beastman_target:
                target_player = self.players.get(beastman_target)
                if target_player and target_player.is_alive:
                    if beastman_target == doctor_target:
                        self.logs.append(f"의사가 누군가를 살려냈습니다.")
                    else:
                        target_player.is_alive = False
                        self.dead_last_night.append(target_player.name)
                        self.logs.append(f"{target_player.name}님이 사망하셨습니다.")

            if not self.dead_last_night:
                self.logs.append("조용하게 밤이 지나갔습니다.")

            # 5. 조사 처리 (스파이 접선 등)
            for player in self.players.values():
                if not player.is_alive or not player.target_id: continue
                target = self.players.get(player.target_id)
                if not target: continue

                if player.role == Role.SPY:
                    if target.role == Role.MAFIA:
                        player.is_contacted = True
                        self.logs.append("스파이가 마피아를 찾아내 접선했습니다.")

            # 상태 초기화
            for player in self.players.values():
                player.target_id = None
                player.is_protected = False

            self.state = GameState.MORNING
            
        except Exception as e:
            logger.error(f"Error processing night actions: {e}")
            raise

    def check_victory(self) -> Optional[Team]:
        # 머릿수 계산 가중치 적용
        mafia_heads = 0
        citizen_heads = 0
        
        real_killers_alive = False # 게임을 계속 진행할 수 있는 킬러(마피아/접선된 짐인)가 있는지
        
        for p in self.players.values():
            if not p.is_alive: continue
            
            # 가중치 계산
            weight = 1
            if p.role == Role.POLITICIAN: weight = 2
            elif p.role == Role.GANGSTER: weight = 3
            
            # 팀 판정
            is_mafia_team = False
            if p.role == Role.MAFIA:
                is_mafia_team = True
                real_killers_alive = True
            elif p.role in [Role.SPY, Role.BEAST_MAN] and p.is_contacted:
                is_mafia_team = True
                if p.role == Role.BEAST_MAN: real_killers_alive = True
            
            if is_mafia_team:
                mafia_heads += weight
            else:
                citizen_heads += weight

        if not real_killers_alive:
            return Team.CITIZEN
        
        if mafia_heads >= citizen_heads:
            return Team.MAFIA
            
        return None

    def get_vote_results(self) -> Dict[str, int]:
        results = {pid: 0 for pid, p in self.players.items() if p.is_alive}
        for pid, player in self.players.items():
            if player.is_alive and player.voted_for:
                # 정치인은 2표
                weight = 2 if player.role == Role.POLITICIAN else 1
                if player.voted_for in results:
                    results[player.voted_for] += weight
        return results

    def reset_votes(self):
        for p in self.players.values():
            p.voted_for = None
            p.votes = 0
            p.is_judgement_yes = None

