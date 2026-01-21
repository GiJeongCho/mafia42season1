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
        self.revealed_role: Optional[Role] = None # 공개된 직업
        self.status_msg: str = "" # 플레이어 상태 메시지 (예: 희생됨)
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
        self.has_skipped = False # 현재 단계에서 스킵 버튼 사용 여부

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
            logger.info(f"Player added to room {self.room_id}: {name} ({player_id})")

    def start_game(self):
        if len(self.players) < 4:
            return False
        self.assign_roles()
        # 설정된 시작 상태에 따라 초기 상태 결정
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
        
        # 1. 마피아 팀 배정 (최소 1명)
        mafia_team_size = max(1, num_players // 4)
        assigned_roles.extend([Role.MAFIA] * mafia_team_size)
        
        # 보조 직업 (5인 이상일 때)
        if num_players >= 5:
            assigned_roles.append(random.choice([Role.SPY, Role.BEAST_MAN]))
            
        # 2. 필수 시민 (경찰, 의사 무조건 1명씩)
        assigned_roles.append(Role.POLICE)
        assigned_roles.append(Role.DOCTOR)
            
        # 3. 시민 팀 - 연인 (인원수가 남고 확률적으로 2명 배정)
        remaining_count = num_players - len(assigned_roles)
        if remaining_count >= 2 and random.random() < 0.3:
            assigned_roles.extend([Role.LOVERS, Role.LOVERS])
            remaining_count -= 2
            
        # 4. 나머지 특수 시민 및 일반 시민
        special_pool = [Role.SOLDIER, Role.POLITICIAN, Role.GANGSTER, 
                        Role.MEDIUM, Role.REPORTER, Role.DETECTIVE]
        random.shuffle(special_pool)
        
        for _ in range(remaining_count):
            if special_pool:
                assigned_roles.append(special_pool.pop())
            else:
                assigned_roles.append(Role.CITIZEN)

        # 인원수에 맞게 최종 셔플 및 자르기 (혹시 모를 에러 방지)
        random.shuffle(assigned_roles)
        assigned_roles = assigned_roles[:num_players]
        
        # 만약 셔플 중 경찰/의사가 누락되었다면 강제 할당 (매우 적은 인원일 때)
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

    def kill_player(self, player: Player, status_msg: str = ""):
        """플레이어 사망 처리 및 직업 공개"""
        if not player.is_alive: return
        player.is_alive = False
        player.revealed_role = player.role
        player.status_msg = status_msg
        self.dead_last_night.append(player.name)

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

            # 2. 기자 특종 처리 (살해 판정 전 취재)
            reporter_p = next((p for p in self.players.values() if p.role == Role.REPORTER and p.is_alive), None)
            if reporter_p and reporter_p.target_id and self.day_count > 1 and not self.reporter_used:
                target_p = self.players.get(reporter_p.target_id)
                if target_p:
                    target_p.revealed_role = target_p.role # 직업 공개
                    self.logs.append(f"[특종] {target_p.name}님의 직업은 [{target_p.role.value}]입니다!")
                    self.reporter_used = True
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
                        self.kill_player(target_player, "짐승인간의 습격")
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
                        self.logs.append(f"군인인 {target_player.name}님이 습격을 버텨냈습니다!")
                    elif target_player.role == Role.BEAST_MAN:
                        self.logs.append(f"누군가 습격을 받았으나 멀쩡합니다.")
                    elif target_player.role == Role.LOVERS:
                        # 연인 희생 로직: 다른 살아있는 연인이 대신 맞음
                        other_lover = next((p for p in self.players.values() if p.role == Role.LOVERS and p.player_id != target_player.player_id and p.is_alive), None)
                        if other_lover:
                            self.kill_player(other_lover, f"연인 {target_player.name}님을 대신해 희생")
                            self.logs.append(f"연인인 {other_lover.name}님이 {target_player.name}님을 대신해 희생하셨습니다.")
                        else:
                            # 혼자 남은 연인이면 그냥 죽음
                            self.kill_player(target_player)
                            self.logs.append(f"{target_player.name}님이 사망하셨습니다. 그의 직업은 [{target_player.role.value}]였습니다.")
                    else:
                        self.kill_player(target_player)
                        self.logs.append(f"{target_player.name}님이 사망하셨습니다. 그의 직업은 [{target_player.role.value}]였습니다.")
            
            # 4. 마피아 전멸 시 접선된 짐승인간의 공격
            if not live_mafias and bm_player and bm_player.is_contacted and beastman_target:
                target_player = self.players.get(beastman_target)
                if target_player and target_player.is_alive:
                    if beastman_target == doctor_target:
                        self.logs.append(f"의사가 누군가를 살려냈습니다.")
                    else:
                        self.kill_player(target_player)
                        self.logs.append(f"{target_player.name}님이 사망하셨습니다. 그의 직업은 [{target_player.role.value}]였습니다.")

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
        """투표권(Voting Power) 기반 승리 조건 계산"""
        mafia_vote_power = 0
        citizen_vote_power = 0
        
        real_killers_alive = False # 게임을 계속 진행할 수 있는 킬러(마피아/접선된 짐인)가 있는지
        
        for p in self.players.values():
            if not p.is_alive: continue
            
            # 1. 투표권(Voting Power) 계산
            vote_power = 0
            if p.is_threatened:
                vote_power = 0 # 협박당하면 투표권 박탈
            elif p.role == Role.POLITICIAN:
                vote_power = 2 # 정치인은 상시 2표
            else:
                vote_power = 1 # 나머지는 기본 1표 (건달 포함)
            
            # 2. 팀 판정 (스파이, 짐인은 접선 여부와 상관없이 마피아 팀의 투표력으로 합산)
            is_mafia_team = False
            if p.role in [Role.MAFIA, Role.SPY, Role.BEAST_MAN]:
                is_mafia_team = True
                # 실질적인 살해 가능 인원 판정
                if p.role == Role.MAFIA or (p.role == Role.BEAST_MAN and p.is_contacted):
                    real_killers_alive = True
            
            if is_mafia_team:
                mafia_vote_power += vote_power
            else:
                citizen_vote_power += vote_power

        # 모든 킬러가 죽으면 시민 승리
        if not real_killers_alive:
            return Team.CITIZEN
        
        # 마피아 팀의 투표권이 시민 팀과 같거나 많아지면 (투표로 통제 가능) 마피아 승리
        if mafia_vote_power >= citizen_vote_power:
            return Team.MAFIA
            
        return None

    def get_vote_results(self) -> Dict[str, int]:
        """투표권 가중치를 반영한 투표 결과 계산"""
        results = {pid: 0 for pid, p in self.players.items() if p.is_alive}
        for pid, player in self.players.items():
            if player.is_alive and player.voted_for:
                # 투표권 가중치 적용
                weight = 1
                if player.is_threatened:
                    weight = 0
                elif player.role == Role.POLITICIAN:
                    weight = 2
                
                if player.voted_for in results:
                    results[player.voted_for] += weight
        return results

    def reset_votes(self):
        """투표 초기화 및 협박 상태 해제"""
        for p in self.players.values():
            p.voted_for = None
            p.votes = 0
            p.is_judgement_yes = None
            p.is_threatened = False # 투표 종료 후 협박 해제

    def reset_skips(self):
        for p in self.players.values():
            p.has_skipped = False

    def has_night_ability(self, role: Role) -> bool:
        """밤에 능동적으로 능력을 사용하는 직업인지 확인"""
        return role in [
            Role.MAFIA, Role.POLICE, Role.DOCTOR, Role.SPY, 
            Role.BEAST_MAN, Role.GANGSTER, Role.DETECTIVE, Role.REPORTER
        ]
