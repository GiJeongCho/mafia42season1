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
        self.is_threatened = False  # 건달에게 협박당함
        self.is_protected = False  # 의사가 살려준 상태 (이번 밤 한정)
        self.is_bulletproof_used = False # 군인 방탄 사용 여부
        self.votes = 0
        self.target_id: Optional[str] = None # 밤에 선택한 대상
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
        
        # 마피아 팀 배정
        mafia_team_size = max(1, num_players // 4)
        assigned_roles.extend([Role.MAFIA] * mafia_team_size)
        if num_players >= 5:
            assigned_roles.append(random.choice([Role.SPY, Role.BEAST_MAN]))
            
        # 필수 시민
        assigned_roles.append(Role.POLICE)
        assigned_roles.append(Role.DOCTOR)
            
        # 연인 (확률)
        remaining_count = num_players - len(assigned_roles)
        if remaining_count >= 2 and random.random() < 0.3:
            assigned_roles.extend([Role.LOVERS, Role.LOVERS])
            remaining_count -= 2
            
        # 나머지 특수 시민
        special_pool = [Role.SOLDIER, Role.POLITICIAN, Role.GANGSTER, 
                        Role.MEDIUM, Role.REPORTER, Role.DETECTIVE]
        random.shuffle(special_pool)
        for _ in range(remaining_count):
            if special_pool: assigned_roles.append(special_pool.pop())
            else: assigned_roles.append(Role.CITIZEN)

        random.shuffle(assigned_roles)
        assigned_roles = assigned_roles[:num_players]
        
        # 보정
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

    def kill_player(self, player: Player, status_msg: str = "", reveal: bool = False, lovers_chain: bool = True):
        """플레이어 사망 처리 및 연쇄 사망(연인) 처리"""
        if not player.is_alive: return
        player.is_alive = False
        if reveal: player.revealed_role = player.role
        player.status_msg = status_msg
        self.dead_last_night.append(player.name)
        
        # 연인 연쇄 사망 로직
        if lovers_chain and player.role == Role.LOVERS:
            other_lover = next((p for p in self.players.values() if p.role == Role.LOVERS and p.player_id != player.player_id and p.is_alive), None)
            if other_lover:
                # 파트너가 죽으면 즉시 자살 처리
                other_lover.is_alive = False
                other_lover.status_msg = f"{player.name}님의 죽음에 비탄에 빠짐"
                self.dead_last_night.append(other_lover.name)
                self.logs.append(f"[{player.name}]님이 사망하자 연인 [{other_lover.name}]님은 슬픔을 못 이기고 자살했습니다.")

    def process_night_actions(self):
        """밤 사이클 판정 우선순위(Priority) 로직 처리"""
        try:
            # 1단계: 마피아 공격 유효성 체크 및 데이터 수집
            doctor_target_id = None
            beastman_target_id = None
            gangster_target_id = None
            reporter_p = next((p for p in self.players.values() if p.role == Role.REPORTER and p.is_alive), None)
            
            for player in self.players.values():
                if not player.is_alive or not player.target_id: continue
                if player.role == Role.DOCTOR: doctor_target_id = player.target_id
                elif player.role == Role.BEAST_MAN: beastman_target_id = player.target_id
                elif player.role == Role.GANGSTER: gangster_target_id = player.target_id

            self.dead_last_night = []
            attack_target_id = self.mafia_target_id
            
            # 건달 협박 처리 (밤에 즉시 상태 변경)
            if gangster_target_id:
                g_target = self.players.get(gangster_target_id)
                if g_target:
                    g_target.is_threatened = True

            # 기자의 '특종' 유효성 체크 (기자가 죽을 예정이면 기사 취소)
            reporter_will_die = False
            if attack_target_id and reporter_p and attack_target_id == reporter_p.player_id:
                # 의사의 힐이 없을 때만 사망 예정
                if attack_target_id != doctor_target_id:
                    reporter_will_die = True

            if reporter_p and reporter_p.target_id and self.day_count > 1 and not self.reporter_used:
                if not reporter_will_die:
                    target_p = self.players.get(reporter_p.target_id)
                    if target_p:
                        target_p.revealed_role = target_p.role
                        self.logs.append(f"[특종] {target_p.name}님의 직업은 [{target_p.role.value}]입니다!")
                        self.reporter_used = True
                else:
                    self.logs.append("기자가 취재를 나갔으나 처참하게 살해당하여 기사가 실리지 못했습니다.")

            # 2단계 & 3단계: 의사의 치료(최우선) vs 고유 능력 방어(군인/짐인)
            if attack_target_id:
                target_p = self.players.get(attack_target_id)
                if target_p:
                    # 짐승인간 접선 로직 (공격 우선순위와 별개로 체크)
                    bm_player = next((p for p in self.players.values() if p.role == Role.BEAST_MAN and p.is_alive), None)
                    if bm_player and not bm_player.is_contacted:
                        # 마피아가 짐인을 쏨
                        if attack_target_id == bm_player.player_id:
                            bm_player.is_contacted = True
                            self.logs.append("짐승인간이 마피아의 총격에서 살아남아 접선했습니다.")
                            attack_target_id = None # 짐인은 안 죽음
                        # 마피아와 짐인이 같은 대상을 쏨 (습격 - 의사 힐 무시)
                        elif beastman_target_id and attack_target_id == beastman_target_id:
                            bm_player.is_contacted = True
                            self.kill_player(target_p, "짐승인간의 습격", reveal=False)
                            self.logs.append(f"짐승인간이 [{target_p.name}]님을 습격하여 접선에 성공했습니다!")
                            attack_target_id = None # 이미 죽음

                    # 살아남은 마피아 공격 판정
                    if attack_target_id:
                        # 2단계: 의사의 힐 (군인 방탄보다 우선)
                        if attack_target_id == doctor_target_id:
                            target_p.is_protected = True
                            self.logs.append(f"의사의 치료로 [{target_p.name}]님이 기사회생하였습니다!")
                        # 3단계: 패시브 방어 (군인 방탄)
                        elif target_p.role == Role.SOLDIER and not target_p.is_bulletproof_used:
                            target_p.is_bulletproof_used = True
                            # 군인에게는 개인 로그가 가야 함 (main.py에서 처리)
                            self.logs.append("지난 밤은 아무 일도 일어나지 않았습니다.")
                        # 3단계: 패시브 방어 (짐승인간 생존)
                        elif target_p.role == Role.BEAST_MAN:
                            self.logs.append("지난 밤은 아무 일도 일어나지 않았습니다.")
                        # 4단계: 최종 사망 처리
                        else:
                            self.kill_player(target_p, reveal=False)
                            self.logs.append(f"지난 밤 [{target_p.name}]님이 처참하게 살해당했습니다.")

            # 마피아 전멸 시 접선된 짐인 공격 (의사 힐 적용)
            live_mafias = [p for p in self.players.values() if p.is_alive and p.role == Role.MAFIA]
            bm_player = next((p for p in self.players.values() if p.role == Role.BEAST_MAN and p.is_alive), None)
            if not live_mafias and bm_player and bm_player.is_contacted and beastman_target_id:
                target_p = self.players.get(beastman_target_id)
                if target_p and target_p.is_alive:
                    if beastman_target_id == doctor_target_id:
                        self.logs.append(f"의사의 치료로 [{target_p.name}]님이 기사회생하였습니다!")
                    else:
                        self.kill_player(target_p, reveal=False)
                        self.logs.append(f"지난 밤 [{target_p.name}]님이 처참하게 살해당했습니다.")

            if not self.dead_last_night and not any("의사의 치료" in log for log in self.logs) and not any("[특종]" in log for log in self.logs):
                if not attack_target_id and not beastman_target_id:
                    self.logs.append("조용하게 밤이 지나갔습니다.")

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
        """투표권 기반 승리 조건 계산 ( Final Ver. )"""
        mafia_power = 0
        citizen_power = 0
        real_killers_alive = False 
        
        for p in self.players.values():
            if not p.is_alive: continue
            
            # 마피아 팀 전멸 여부 확인
            if p.role == Role.MAFIA or (p.role == Role.BEAST_MAN and p.is_contacted):
                real_killers_alive = True
            
            # 투표권 계산
            p_power = 0
            if p.is_threatened: p_power = 0
            elif p.role == Role.POLITICIAN: p_power = 2 
            else: p_power = 1
            
            # 팀 분류
            if p.role in [Role.MAFIA, Role.SPY, Role.BEAST_MAN]:
                mafia_power += p_power
            else:
                citizen_power += p_power

        if not real_killers_alive:
            return Team.CITIZEN 
        
        if mafia_power >= citizen_power:
            return Team.MAFIA
            
        return None

    def get_vote_results(self) -> Dict[str, int]:
        results = {pid: 0 for pid, p in self.players.items() if p.is_alive}
        for pid, player in self.players.items():
            if player.is_alive and player.voted_for:
                weight = 1
                if player.is_threatened: weight = 0
                elif player.role == Role.POLITICIAN: weight = 2
                if player.voted_for in results:
                    results[player.voted_for] += weight
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
