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
    def __init__(self, player_id: str, name: str, number: int):
        self.player_id = player_id
        self.name = name
        self.number = number 
        self.role: Optional[Role] = None
        self.revealed_role: Optional[Role] = None 
        self.status_msg: str = "" 
        self.is_alive = True
        self.is_contacted = False  
        self.is_threatened = False  
        self.is_protected = False  
        self.is_bulletproof_used = False 
        self.votes = 0
        self.target_id: Optional[str] = None 
        self.voted_for: Optional[str] = None 
        self.memos: Dict[str, str] = {} 
        self.is_judgement_yes: Optional[bool] = None 
        self.has_skipped = False 

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
        self.mafia_target_id: Optional[str] = None 
        self.settings = {
            "start_state": "NIGHT",
            "night_duration": 30,
            "day_duration": 90
        }

    def add_player(self, player_id: str, name: str):
        if player_id not in self.players:
            number = len(self.players) + 1
            player = Player(player_id, name, number)
            self.players[player_id] = player
            if not self.host_id:
                self.host_id = player_id
            logger.info(f"Player added to room {self.room_id}: {name} ({player_id})")

    def start_game(self):
        if len(self.players) < 4:
            return False
        self.assign_roles()
        start_state = self.settings.get("start_state", "NIGHT")
        self.state = GameState.NIGHT if start_state == "NIGHT" else GameState.DAY
        self.day_count = 1
        self.logs = []
        return True

    def assign_roles(self):
        player_ids = list(self.players.keys())
        num_players = len(player_ids)
        random.shuffle(player_ids)
        assigned_roles = []
        
        # 1. 마피아 팀 배정
        mafia_team_size = max(1, num_players // 4)
        assigned_roles.extend([Role.MAFIA] * mafia_team_size)
        if num_players >= 5:
            assigned_roles.append(random.choice([Role.SPY, Role.BEAST_MAN]))
            
        # 2. 필수 시민
        assigned_roles.append(Role.POLICE)
        assigned_roles.append(Role.DOCTOR)
            
        # 3. 연인 (확률)
        remaining_count = num_players - len(assigned_roles)
        if remaining_count >= 2 and random.random() < 0.3:
            assigned_roles.extend([Role.LOVERS, Role.LOVERS])
            remaining_count -= 2
            
        # 4. 나머지 특수 시민
        special_pool = [Role.SOLDIER, Role.POLITICIAN, Role.GANGSTER, 
                        Role.MEDIUM, Role.REPORTER, Role.DETECTIVE]
        random.shuffle(special_pool)
        for _ in range(remaining_count):
            if special_pool: assigned_roles.append(special_pool.pop())
            else: assigned_roles.append(Role.CITIZEN)

        random.shuffle(assigned_roles)
        assigned_roles = assigned_roles[:num_players]
        
        if Role.POLICE not in assigned_roles: assigned_roles[0] = Role.POLICE
        if Role.DOCTOR not in assigned_roles: assigned_roles[1] = Role.DOCTOR
        
        random.shuffle(assigned_roles)
        for i, pid in enumerate(player_ids):
            self.players[pid].role = assigned_roles[i]
            self.players[pid].is_alive = True
            self.players[pid].revealed_role = None
            self.players[pid].status_msg = ""
            self.players[pid].is_contacted = False
            self.players[pid].is_threatened = False
            self.players[pid].is_protected = False
            self.players[pid].is_bulletproof_used = False
            self.players[pid].votes = 0
            self.players[pid].target_id = None
            self.players[pid].voted_for = None
            self.players[pid].memos = {other_pid: "" for other_pid in player_ids}
            self.players[pid].has_skipped = False

    def kill_player(self, player: Player, status_msg: str = "", reveal: bool = False):
        """플레이어 사망 처리"""
        if not player.is_alive: return
        player.is_alive = False
        if reveal: player.revealed_role = player.role
        player.status_msg = status_msg
        self.dead_last_night.append(player.name)

    def process_night_actions(self):
        """밤 사이클 로직 처리 (우선순위 고려)"""
        try:
            mafia_target = None
            doctor_target = None
            beastman_target = None
            reporter_p = next((p for p in self.players.values() if p.role == Role.REPORTER and p.is_alive), None)
            live_mafias = [p for p in self.players.values() if p.is_alive and p.role == Role.MAFIA]
            
            # 1. 행동 타겟 수집
            for player in self.players.values():
                if not player.is_alive or not player.target_id: continue
                if player.role == Role.MAFIA: mafia_target = player.target_id
                elif player.role == Role.DOCTOR: doctor_target = player.target_id
                elif player.role == Role.BEAST_MAN: beastman_target = player.target_id
                elif player.role == Role.GANGSTER:
                    target = self.players.get(player.target_id)
                    if target: target.is_threatened = True

            self.dead_last_night = []
            
            # 2. 기자 특종 (살해 판정 전)
            reporter_will_die = (self.mafia_target_id == reporter_p.player_id and self.mafia_target_id != doctor_target) if reporter_p and self.mafia_target_id else False
            if reporter_p and reporter_p.target_id and self.day_count > 1 and not self.reporter_used:
                if not reporter_will_die:
                    target_p = self.players.get(reporter_p.target_id)
                    if target_p:
                        target_p.revealed_role = target_p.role
                        self.logs.append(f"[특종] {target_p.name}님의 직업은 [{target_p.role.value}]입니다!")
                        self.reporter_used = True
                else:
                    self.logs.append("기자가 취재를 나갔으나 처참하게 살해당하여 기사가 실리지 못했습니다.")

            # 3. 짐승인간 접선 및 공격 특수 로직
            bm_player = next((p for p in self.players.values() if p.role == Role.BEAST_MAN and p.is_alive), None)
            if bm_player and not bm_player.is_contacted:
                if self.mafia_target_id == bm_player.player_id:
                    bm_player.is_contacted = True
                    self.logs.append("짐승인간이 마피아를 찾아내 접선했습니다.")
                    self.mafia_target_id = None
                elif self.mafia_target_id and self.mafia_target_id == beastman_target:
                    bm_player.is_contacted = True
                    target_player = self.players.get(self.mafia_target_id)
                    if target_player:
                        self.kill_player(target_player, "짐승인간의 습격", reveal=False)
                        self.logs.append(f"짐승인간이 {target_player.name}님을 습격하며 마피아와 접선했습니다!")
                    self.mafia_target_id = None

            # 4. 일반 마피아 공격 판정
            if self.mafia_target_id:
                target_player = self.players.get(self.mafia_target_id)
                if target_player:
                    if self.mafia_target_id == doctor_target:
                        self.logs.append(f"의사가 누군가를 살려냈습니다.")
                    elif target_player.role == Role.SOLDIER and not target_player.is_bulletproof_used:
                        target_player.is_bulletproof_used = True
                        self.logs.append("지난 밤은 아무 일도 일어나지 않았습니다.")
                    elif target_player.role == Role.BEAST_MAN:
                        self.logs.append("지난 밤은 아무 일도 일어나지 않았습니다.")
                    elif target_player.role == Role.LOVERS:
                        # 연인 대신 죽기 로직
                        other_lover = next((p for p in self.players.values() if p.role == Role.LOVERS and p.player_id != target_player.player_id and p.is_alive), None)
                        if other_lover:
                            self.kill_player(other_lover, f"연인 {target_player.name}님을 대신해 희생", reveal=False)
                            self.logs.append(f"연인인 {other_lover.name}님이 {target_player.name}님을 대신해 희생하셨습니다.")
                        else:
                            self.kill_player(target_player, reveal=False)
                            self.logs.append(f"[{target_player.name}]님이 사망하셨습니다.")
                    else:
                        self.kill_player(target_player, reveal=False)
                        self.logs.append(f"[{target_player.name}]님이 사망하셨습니다.")
            
            # 5. 마피아 전멸 시 접선된 짐승인간의 공격
            if not live_mafias and bm_player and bm_player.is_contacted and beastman_target:
                target_player = self.players.get(beastman_target)
                if target_player and target_player.is_alive:
                    if beastman_target == doctor_target: self.logs.append(f"의사가 누군가를 살려냈습니다.")
                    else:
                        self.kill_player(target_player, reveal=False)
                        self.logs.append(f"[{target_player.name}]님이 사망하셨습니다.")

            if not self.dead_last_night and not any("살려냈습니다" in log for log in self.logs) and not any("[특종]" in log for log in self.logs):
                self.logs.append("조용하게 밤이 지나갔습니다.")

            # 6. 조사 처리 (스파이 접선 등)
            for player in self.players.values():
                if not player.is_alive or not player.target_id: continue
                target = self.players.get(player.target_id)
                if not target: continue
                if player.role == Role.SPY and target.role == Role.MAFIA:
                    player.is_contacted = True
                    self.logs.append("스파이가 마피아를 찾아내 접선했습니다.")

            # 상태 초기화
            for player in self.players.values():
                player.target_id = None
                player.is_protected = False
            self.mafia_target_id = None
            self.state = GameState.MORNING
            
        except Exception as e:
            logger.error(f"Error processing night actions: {e}")
            raise

    def check_victory(self) -> Optional[Team]:
        # 머릿수 계산
        mafia_heads = 0
        citizen_heads = 0
        real_killers_alive = False 
        for p in self.players.values():
            if not p.is_alive: continue
            weight = 1
            if p.role == Role.POLITICIAN: weight = 2
            elif p.role == Role.GANGSTER: weight = 3
            is_mafia_team = False
            if p.role == Role.MAFIA:
                is_mafia_team = True
                real_killers_alive = True
            elif p.role in [Role.SPY, Role.BEAST_MAN] and p.is_contacted:
                is_mafia_team = True
                if p.role == Role.BEAST_MAN: real_killers_alive = True
            if is_mafia_team: mafia_heads += weight
            else: citizen_heads += weight
        if not real_killers_alive: return Team.CITIZEN
        if mafia_heads >= citizen_heads: return Team.MAFIA
        return None

    def get_vote_results(self) -> Dict[str, int]:
        results = {pid: 0 for pid, p in self.players.items() if p.is_alive}
        for pid, player in self.players.items():
            if player.is_alive and player.voted_for:
                weight = 2 if player.role == Role.POLITICIAN else 1
                if player.voted_for in results: results[player.voted_for] += weight
        return results

    def reset_votes(self):
        for p in self.players.values():
            p.voted_for = None
            p.votes = 0
            p.is_judgement_yes = None
            p.is_threatened = False 

    def reset_skips(self):
        for p in self.players.values():
            p.has_skipped = False

    def reset_game_state(self):
        self.state = GameState.WAITING
        self.day_count = 1
        self.dead_last_night = []
        self.logs = []
        self.reporter_used = False
        self.timer = 0
        self.nominee_id = None
        self.mafia_target_id = None
        for p in self.players.values():
            p.role = None
            p.revealed_role = None
            p.status_msg = ""
            p.is_alive = True
            p.is_contacted = False
            p.is_threatened = False
            p.is_protected = False
            p.is_bulletproof_used = False
            p.votes = 0
            p.target_id = None
            p.voted_for = None
            p.has_skipped = False

    def has_night_ability(self, role: Role) -> bool:
        return role in [
            Role.MAFIA, Role.POLICE, Role.DOCTOR, Role.SPY, 
            Role.BEAST_MAN, Role.GANGSTER, Role.DETECTIVE, Role.REPORTER
        ]
