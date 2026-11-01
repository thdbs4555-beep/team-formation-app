"""앱의 동작. 각 함수는 결과 안내 문자열을 돌려주며, 첫 단어는 승인/대기/거절/막힘이다."""
import csv
import io
import json
import time
from dataclasses import dataclass, field

import bcrypt

from . import ai
from .rules import (ROLES, Student, check_assignment, explain_no_solution,
                    join_problem, solve)

ADMIN_NO = "admin"
DEFAULT_PASSWORD = "1234"
ASSIGN_STALE_SEC = 480


@dataclass
class State:
    students: dict = field(default_factory=dict)          # id -> Student
    teams: dict = field(default_factory=dict)             # team_no -> 팀원 Student 목록
    team_ids: dict = field(default_factory=dict)          # team_no -> tf_teams.id
    pending: list = field(default_factory=list)           # 대기 중인 지원
    votes: dict = field(default_factory=dict)             # application_id -> {voter_id: vote}
    settings: dict = field(default_factory=dict)

    @property
    def closed(self):
        return self.settings.get("closed") == "1"

    @property
    def confirmed(self):
        return self.settings.get("confirmed") == "1"

    @property
    def proposal(self):
        raw = self.settings.get("proposal")
        return json.loads(raw) if raw else None

    def team_of(self, student_id):
        for no, members in self.teams.items():
            if any(m.id == student_id for m in members):
                return no
        return None

    def pending_of(self, student_id):
        return next((a for a in self.pending if a["student_id"] == student_id), None)

    @property
    def unassigned(self):
        return [s for s in self.students.values() if self.team_of(s.id) is None]


def hash_password(password):
    return bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt(rounds=10)).decode()


def set_setting(c, key, value):
    c.run("insert into tf_settings (key, value) values (%s, %s) "
          "on conflict (key) do update set value = excluded.value", (key, value))


def load_state(c):
    state = State()
    subs = {}
    for row in c.rows("select student_id, role from tf_student_sub_roles order by role"):
        subs.setdefault(row["student_id"], []).append(row["role"])
    for row in c.rows("select id, student_no, name, main_role from tf_students "
                      "where not is_admin order by student_no"):
        state.students[row["id"]] = Student(row["id"], row["student_no"], row["name"],
                                            row["main_role"], tuple(subs.get(row["id"], ())))
    for row in c.rows("select id, team_no from tf_teams order by team_no"):
        state.teams[row["team_no"]] = []
        state.team_ids[row["team_no"]] = row["id"]
    numbers = {v: k for k, v in state.team_ids.items()}
    for row in c.rows("select team_id, student_id from tf_members order by slot_no"):
        state.teams[numbers[row["team_id"]]].append(state.students[row["student_id"]])
    for row in c.rows("select id, team_id, student_id from tf_applications "
                      "where status = '대기' order by id"):
        row["team_no"] = numbers[row["team_id"]]
        state.pending.append(row)
    for row in c.rows("select application_id, voter_id, vote from tf_votes"):
        state.votes.setdefault(row["application_id"], {})[row["voter_id"]] = row["vote"]
    state.settings = {r["key"]: r["value"] for r in c.rows("select key, value from tf_settings")}
    return state


def add_member(c, team_id, student_id):
    """빈 슬롯에 넣는다. 동시에 들어온 학생은 기본 키 충돌로 DB가 막는다."""
    used = {r["slot_no"] for r in c.rows("select slot_no from tf_members where team_id = %s", (team_id,))}
    slot = min(set(range(1, 5)) - used)
    c.run("insert into tf_members (team_id, slot_no, student_id) values (%s, %s, %s)",
          (team_id, slot, student_id))


class Service:
    def __init__(self, db):
        self.db = db

    def snapshot(self):
        with self.db.tx() as c:
            return load_state(c)

    def ensure_admin(self, password):
        with self.db.tx(lock=True) as c:
            row = c.one("select id, password_hash from tf_students where student_no = %s", (ADMIN_NO,))
            if row is None:
                c.run("insert into tf_students (student_no, name, password_hash, is_admin) "
                      "values (%s, %s, %s, true)", (ADMIN_NO, "관리자", hash_password(password)))
            elif not bcrypt.checkpw(password.encode()[:72], row["password_hash"].encode()):
                c.run("update tf_students set password_hash = %s where id = %s",
                      (hash_password(password), row["id"]))

    def login(self, student_no, password):
        """맞으면 계정 정보, 틀리면 None."""
        with self.db.tx() as c:
            row = c.one("select student_no, name, main_role, password_hash, is_admin "
                        "from tf_students where student_no = %s", (student_no.strip(),))
        if row and bcrypt.checkpw(password.encode()[:72], row["password_hash"].encode()):
            return {k: row[k] for k in ("student_no", "name", "main_role", "is_admin")}
        return None

    def find_user(self, student_no):
        with self.db.tx() as c:
            return c.one("select id, student_no, name, main_role, is_admin "
                         "from tf_students where student_no = %s", (student_no,))

    # 명단

    def start(self, raw, team_count):
        try:
            roster = parse_roster(raw)
        except ValueError as error:
            return f"막힘: {error}"
        if team_count < 1:
            return "막힘: 팀 수는 1 이상이어야 함"
        rows = [(no, name, main, subs, hash_password(DEFAULT_PASSWORD))
                for no, name, main, subs in roster]
        with self.db.tx(lock=True) as c:
            for table in ("tf_votes", "tf_applications", "tf_members", "tf_teams",
                          "tf_student_sub_roles", "tf_settings", "tf_ai_logs"):
                c.run(f"delete from {table}")
            c.run("delete from tf_students where not is_admin")
            for no, name, main, subs, hashed in rows:
                new_id = c.one("insert into tf_students (student_no, name, main_role, password_hash) "
                               "values (%s, %s, %s, %s) returning id", (no, name, main, hashed))["id"]
                for role in subs:
                    c.run("insert into tf_student_sub_roles (student_id, role) values (%s, %s)",
                          (new_id, role))
            for no in range(1, team_count + 1):
                c.run("insert into tf_teams (team_no, name) values (%s, %s)", (no, f"팀{no}"))
        return f"승인: 학생 {len(rows)}명과 팀 {team_count}개로 시작함"

    # 지원과 투표

    def apply(self, student_id, team_no):
        try:
            with self.db.tx(lock=True) as c:
                state = load_state(c)
                student = state.students.get(student_id)
                if student is None or team_no not in state.teams:
                    return "막힘: 없는 학생이나 팀임"
                if state.closed:
                    return "막힘: 모집이 마감됨"
                if state.team_of(student_id) is not None:
                    return "막힘: 이미 팀이 있음"
                if state.pending_of(student_id):
                    return "막힘: 결과를 기다리는 지원이 있음"
                members = state.teams[team_no]
                problem = join_problem(members, student)
                if problem:
                    return f"막힘: {problem}"
                team_id = state.team_ids[team_no]
                status = "대기" if members else "승인"
                c.run("insert into tf_applications (team_id, student_id, status) values (%s, %s, %s)",
                      (team_id, student_id, status))
                if members:
                    return f"대기: 팀{team_no} 팀원의 투표를 기다림"
                add_member(c, team_id, student_id)
                return f"승인: 팀{team_no}의 팀원이 됨"
        except self.db.integrity_error:
            return "막힘: 다른 학생이 먼저 들어와 자리가 없음"

    def vote(self, voter_id, applicant_no, agree):
        try:
            with self.db.tx(lock=True) as c:
                return self._vote(c, voter_id, applicant_no, agree)
        except self.db.integrity_error:
            return "거절: 다른 학생이 먼저 들어와 자리가 없음"

    def _vote(self, c, voter_id, applicant_no, agree):
        state = load_state(c)
        if state.closed:
            return "막힘: 모집이 마감됨"
        team_no = state.team_of(voter_id)
        application = next(
            (a for a in state.pending
             if state.students[a["student_id"]].student_no == applicant_no and a["team_no"] == team_no),
            None)
        if team_no is None or application is None:
            return "막힘: 투표할 수 있는 지원이 없음"
        votes = state.votes.get(application["id"], {})
        if voter_id in votes:
            return "막힘: 이미 투표함"
        c.run("insert into tf_votes (application_id, voter_id, vote) values (%s, %s, %s)",
              (application["id"], voter_id, "찬성" if agree else "거부"))

        def reject(reason):
            c.run("update tf_applications set status = '거절', reason = %s where id = %s",
                  (reason, application["id"]))
            return f"거절: {reason}"

        if not agree:
            return reject("팀원이 거부함")
        members = state.teams[team_no]
        agreed = {i for i, v in votes.items() if v == "찬성"} | {voter_id}
        if not all(m.id in agreed for m in members):
            return "대기: 아직 투표하지 않은 팀원이 있음"
        # 먼저 승인된 지원으로 팀이 바뀌었을 수 있으므로 승인 직전에 다시 검사한다.
        problem = join_problem(members, state.students[application["student_id"]])
        if problem:
            return reject(f"승인 직전 검사에서 걸림({problem})")
        c.run("update tf_applications set status = '승인' where id = %s", (application["id"],))
        add_member(c, application["team_id"], application["student_id"])
        return "승인: 모든 팀원이 찬성해 팀원이 됨"

    # 마감과 배정

    def close(self):
        with self.db.tx(lock=True) as c:
            state = load_state(c)
            if not state.students:
                return "막힘: 명단을 먼저 올려야 함"
            if state.closed:
                return "막힘: 이미 마감됨"
            c.run("update tf_applications set status = '거절', reason = '모집 마감' where status = '대기'")
            for no, members in state.teams.items():
                if len(members) == 1:
                    c.run("delete from tf_members where student_id = %s", (members[0].id,))
            set_setting(c, "closed", "1")
        return "승인: 모집을 마감함"

    def begin_assign(self):
        """배정을 시작해도 되는지 보고, 되면 진행 표시를 켠다. 안 되면 막힘 안내를 돌려준다."""
        with self.db.tx(lock=True) as c:
            state = load_state(c)
            if not state.closed:
                return "막힘: 모집을 먼저 마감해야 함"
            if state.confirmed:
                return "막힘: 이미 확정함"
            if assign_running(state):
                return "막힘: 배정이 진행 중임"
            set_setting(c, "assign_started", str(time.time()))
            set_setting(c, "assign_status", "배정을 준비하는 중")
            c.run("delete from tf_settings where key in ('proposal', 'assign_result')")
        return None

    def _finish_assign(self, status, result, proposal=None):
        with self.db.tx(lock=True) as c:
            set_setting(c, "assign_status", status)
            set_setting(c, "assign_result", result)
            if proposal:
                set_setting(c, "proposal", json.dumps(proposal, ensure_ascii=False))
            c.run("delete from tf_settings where key = 'assign_started'")

    def _progress(self, text):
        with self.db.tx() as c:
            set_setting(c, "assign_status", text)

    def _log(self, attempt, model, outcome, detail, elapsed):
        with self.db.tx() as c:
            c.run("insert into tf_ai_logs (attempt, model, outcome, detail, elapsed_sec) "
                  "values (%s, %s, %s, %s, %s)", (attempt, model, outcome, detail, round(elapsed, 1)))

    def run_assign(self, api_key, call=ai.timed_call, models=ai.MODELS):
        """AI 1~3차를 차례로 시도하고, 모두 실패하면 앱이 직접 찾는다. 배경 스레드에서 돈다."""
        try:
            self._run_assign(api_key, call, models)
        except Exception as error:
            self._finish_assign("배정 중 오류가 남", f"막힘: 배정 중 오류가 남({type(error).__name__})")

    def _run_assign(self, api_key, call, models):
        state = self.snapshot()
        teams, unassigned = state.teams, state.unassigned
        fallback = solve(teams, unassigned)
        if fallback is None:
            reason = explain_no_solution(teams, unassigned)
            text = f"규칙을 지키는 배정이 없음: {reason}"
            self._finish_assign(text, f"막힘: {text}")
            return
        if not unassigned:
            self._finish_assign("팀이 없는 학생이 없어 배정할 것이 없음", "승인: 배정할 학생이 없음",
                                {"source": "배정할 학생 없음", "pairs": []})
            return
        prompt = ai.build_prompt(teams, unassigned)
        for attempt, model in enumerate(models, 1):
            self._progress(f"{attempt}차 모델({model})의 응답을 기다리는 중 (최대 {ai.MODEL_LIMIT_SEC}초)")
            content, error, elapsed = call(model, prompt, api_key)
            if error:
                self._log(attempt, model, "호출 실패", error, elapsed)
                continue
            try:
                pairs = ai.parse_pairs(content)
            except ValueError as parse_error:
                self._log(attempt, model, "응답 형식 오류", str(parse_error), elapsed)
                continue
            problems = check_assignment(teams, unassigned, pairs)
            if problems:
                self._log(attempt, model, "검사 불합격", "; ".join(problems)[:500], elapsed)
                continue
            self._log(attempt, model, "성공", None, elapsed)
            source = f"{attempt}차 모델({model})"
            self._finish_assign(f"{source}의 배정안이 검사를 통과함. 확인 후 확정하세요",
                                f"승인: {source}의 배정안이 검사를 통과함",
                                {"source": source, "pairs": pairs})
            return
        self._finish_assign("3차 모델까지 실패해 앱이 규칙을 지키는 배정을 직접 찾음. 확인 후 확정하세요",
                            "승인: 3차 모델까지 실패해 앱이 직접 배정함",
                            {"source": "앱의 직접 배정", "pairs": fallback})

    def confirm(self):
        try:
            with self.db.tx(lock=True) as c:
                state = load_state(c)
                if state.confirmed:
                    return "막힘: 이미 확정함"
                proposal = state.proposal
                if proposal is None:
                    return "막힘: 확정할 배정안이 없음"
                pairs = [(no, team) for no, team in proposal["pairs"]]
                problems = check_assignment(state.teams, state.unassigned, pairs)
                if problems:
                    return f"막힘: 배정안이 규칙을 어김({problems[0]})"
                by_no = {s.student_no: s for s in state.students.values()}
                for student_no, team_no in pairs:
                    add_member(c, state.team_ids[team_no], by_no[student_no].id)
                set_setting(c, "confirmed", "1")
                set_setting(c, "assign_status", "배정을 확정함")
                c.run("delete from tf_settings where key = 'proposal'")
            return "승인: 배정을 확정함"
        except self.db.integrity_error:
            return "막힘: 배정안과 팀 구성이 달라져 확정하지 못함"

    def ai_logs(self):
        with self.db.tx() as c:
            return c.rows("select attempt, model, outcome, detail, elapsed_sec "
                          "from tf_ai_logs order by id desc limit 12")


def assign_running(state):
    started = state.settings.get("assign_started")
    return bool(started) and time.time() - float(started) < ASSIGN_STALE_SEC


def parse_roster(raw):
    """명단 CSV를 읽는다. 틀린 곳이 있으면 ValueError."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("CSV는 UTF-8이어야 함") from error
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any(cell.strip() for cell in r)]
    if not rows or [c.strip() for c in rows[0]] != ["student_no", "name", "main_role", "sub_roles"]:
        raise ValueError("CSV 머리글은 student_no,name,main_role,sub_roles 이어야 함")
    roster, seen = [], set()
    for line, row in enumerate(rows[1:], 2):
        if len(row) != 4:
            raise ValueError(f"{line}번째 줄의 열이 4개가 아님")
        no, name, main, subs = (c.strip() for c in row)
        sub_roles = [s.strip() for s in subs.split(";") if s.strip()]
        if not no or not name or no == ADMIN_NO or no in seen:
            raise ValueError(f"{line}번째 줄의 학번이나 이름이 틀림")
        if main not in ROLES or any(s not in ROLES or s == main for s in sub_roles):
            raise ValueError(f"{line}번째 줄의 역할이 틀림")
        if len(sub_roles) > 2 or len(set(sub_roles)) != len(sub_roles):
            raise ValueError(f"{line}번째 줄의 보조 역할이 틀림")
        seen.add(no)
        roster.append((no, name, main, sub_roles))
    if not roster:
        raise ValueError("명단에 학생이 없음")
    return roster
