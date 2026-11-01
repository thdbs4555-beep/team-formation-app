"""팀 규칙과 배정 탐색. DB와 화면에 의존하지 않는 순수 로직이다."""
from dataclasses import dataclass

PLANNER = "기획자"
WEB = "웹 프로그래머"
ROLES = (PLANNER, WEB, "프론트엔드 디자이너", "테스터", "보고서 작성자", "발표자")
MAX_MEMBERS = 4
MIN_MEMBERS = 2


@dataclass(frozen=True)
class Student:
    id: int
    student_no: str
    name: str
    main_role: str
    sub_roles: tuple = ()

    def can(self, role):
        return role == self.main_role or role in self.sub_roles


def filled_required(members):
    """필수 포함 역할 2개 가운데 서로 다른 학생으로 채운 수(0~2)."""
    planners = [s for s in members if s.can(PLANNER)]
    webs = [s for s in members if s.can(WEB)]
    if any(p is not w and p.id != w.id for p in planners for w in webs):
        return 2
    return 1 if planners or webs else 0


def seats_suffice(members):
    """남는 자리로 아직 못 채운 필수 포함 역할을 채울 수 있는가."""
    return MAX_MEMBERS - len(members) >= 2 - filled_required(members)


def join_problem(members, student):
    """학생이 팀에 들어갈 수 없는 이유. 들어갈 수 있으면 None."""
    if len(members) >= MAX_MEMBERS:
        return "팀이 꽉 참"
    if any(m.main_role == student.main_role for m in members):
        return "같은 주 역할의 팀원이 있음"
    if not seats_suffice([*members, student]):
        return "필수 포함 역할을 맡을 학생의 자리가 모자람"
    return None


def team_problems(team_no, members):
    """완성된 팀이 2.3절 규칙을 어기는 내용."""
    problems = []
    if not MIN_MEMBERS <= len(members) <= MAX_MEMBERS:
        problems.append(f"팀{team_no}의 인원이 {len(members)}명임")
    if len({m.main_role for m in members}) != len(members):
        problems.append(f"팀{team_no}에 주 역할이 중복됨")
    if filled_required(members) < 2:
        problems.append(f"팀{team_no}에 필수 포함 역할이 모자람")
    return problems


def check_assignment(teams, unassigned, pairs):
    """배정안 검사 다섯 가지. pairs는 (학번, 팀 번호) 목록이고, 어긴 내용을 돌려준다."""
    by_no = {s.student_no: s for s in unassigned}
    problems = []
    seen = set()
    result = {no: list(members) for no, members in teams.items()}
    for student_no, team_no in pairs:
        if student_no not in by_no:
            problems.append(f"{student_no}는 팀이 없는 학생이 아님")
        elif student_no in seen:
            problems.append(f"{by_no[student_no].name}이 여러 번 배정됨")
        elif team_no not in result:
            problems.append(f"팀{team_no}은 없는 팀임")
        else:
            seen.add(student_no)
            result[team_no].append(by_no[student_no])
    for s in unassigned:
        if s.student_no not in seen:
            problems.append(f"{s.name}이 배정되지 않음")
    for team_no, members in sorted(result.items()):
        problems.extend(team_problems(team_no, members))
    return problems


def solve(teams, unassigned):
    """다섯 가지 검사를 모두 통과하는 배정을 끝까지 찾는다. 없으면 None."""
    order = sorted(unassigned, key=lambda s: sum(
        join_problem(m, s) is None for m in teams.values()))
    current = {no: list(members) for no, members in teams.items()}
    pairs = []

    def search(i):
        if i == len(order):
            return not any(team_problems(no, m) for no, m in current.items())
        student = order[i]
        for no, members in sorted(current.items()):
            if join_problem(members, student) is None:
                members.append(student)
                pairs.append((student.student_no, no))
                if search(i + 1):
                    return True
                pairs.pop()
                members.pop()
        return False

    return list(pairs) if search(0) else None


def explain_no_solution(teams, unassigned):
    """규칙을 지키는 배정이 없을 때 관리자에게 보여 줄 이유."""
    seats = sum(MAX_MEMBERS - len(m) for m in teams.values())
    if len(unassigned) > seats:
        return f"남은 학생 {len(unassigned)}명보다 빈자리 {seats}개가 적음"
    needed = sum(max(0, MIN_MEMBERS - len(m)) for m in teams.values())
    if len(unassigned) < needed:
        return f"모든 팀을 2명 이상으로 채우려면 {needed}명이 필요한데 남은 학생은 {len(unassigned)}명임"
    for no, members in sorted(teams.items()):
        pool = [s for s in unassigned
                if not any(m.main_role == s.main_role for m in members)]
        for role in (PLANNER, WEB):
            if not any(m.can(role) for m in members) and not any(s.can(role) for s in pool):
                return f"팀{no}에 {role}를 맡을 학생이 없음"
        if len(members) >= MAX_MEMBERS and filled_required(members) < 2:
            return f"팀{no}이 꽉 찼지만 필수 포함 역할이 모자람"
    return "모든 경우를 따져 보았지만 인원, 주 역할 중복, 필수 포함 역할을 함께 만족하는 배정이 없음"
