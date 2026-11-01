"""공고 부록의 연습용 시나리오와 규칙 검사. Supabase 없이 SQLite로 돌린다."""
import json
import os
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from teamapp import ai
from teamapp.db import PostgresDb, SqliteDb
from teamapp.rules import Student, check_assignment, explain_no_solution, solve, team_problems
from teamapp.service import Service, parse_roster


ADMIN_PASSWORD = "test-admin"
SHARED_DB = None


def make_db():
    """RUN_PG=1이면 `.env`의 실제 DB를 쓴다. 이때 tf_ 테이블의 내용은 지워진다."""
    global ADMIN_PASSWORD, SHARED_DB
    if not os.environ.get("RUN_PG"):
        return SqliteDb()
    if SHARED_DB is None:
        load_dotenv(ROOT / ".env")
        ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]
        SHARED_DB = PostgresDb(os.environ["DATABASE_URL"])
    return SHARED_DB


def fails(model, prompt, key):
    return None, "HTTP 429", 0.1


class Game:
    def __init__(self, csv_name, team_count):
        self.s = Service(make_db())
        self.s.ensure_admin(ADMIN_PASSWORD)
        raw = (ROOT / "data" / csv_name).read_bytes()
        assert self.s.start(raw, team_count).startswith("승인")
        self.ids = {s.student_no: s.id for s in self.s.snapshot().students.values()}

    def apply(self, no, team):
        return self.s.apply(self.ids[str(no)], team).split(":")[0]

    def vote(self, voter, applicant, agree=True):
        return self.s.vote(self.ids[str(voter)], str(applicant), agree).split(":")[0]

    def assign_and_confirm(self, call=fails):
        assert self.s.begin_assign() is None
        self.s.run_assign("key", call=call)
        state = self.s.snapshot()
        assert state.settings["assign_result"].startswith("승인"), state.settings
        assert self.s.confirm().startswith("승인")
        state = self.s.snapshot()
        assert not state.unassigned
        for no, members in state.teams.items():
            assert team_problems(no, members) == []
        return state


def test_easy():
    g = Game("쉬움.csv", 3)
    assert g.apply(2026101, 1) == "승인"
    assert g.apply(2026102, 2) == "승인"
    assert g.apply(2026103, 3) == "승인"
    assert g.apply(2026104, 1) == "대기"
    assert g.vote(2026101, 2026104) == "승인"
    assert g.apply(2026105, 2) == "대기"
    assert g.vote(2026102, 2026105) == "승인"
    assert g.apply(2026107, 1) == "대기"
    assert g.vote(2026101, 2026107) == "대기"
    assert g.vote(2026104, 2026107, False) == "거절"
    assert g.apply(2026106, 3) == "대기"
    assert g.vote(2026103, 2026106) == "승인"
    assert g.s.close().startswith("승인")
    g.assign_and_confirm()


def test_medium():
    g = Game("중간.csv", 4)
    assert g.apply(2026201, 1) == "승인"
    assert g.apply(2026205, 1) == "대기"
    assert g.vote(2026201, 2026205) == "승인"
    assert g.apply(2026202, 1) == "막힘"
    assert g.apply(2026206, 2) == "승인"
    assert g.apply(2026209, 2) == "대기"
    assert g.vote(2026206, 2026209) == "승인"
    assert g.apply(2026210, 2) == "대기"
    assert g.vote(2026206, 2026210) == "대기"
    assert g.vote(2026209, 2026210, False) == "거절"
    assert g.apply(2026203, 3) == "승인"
    assert g.apply(2026211, 4) == "승인"
    assert g.apply(2026212, 4) == "대기"
    assert g.apply(2026212, 3) == "막힘"
    assert g.s.close().startswith("승인")
    state = g.s.snapshot()
    assert not state.pending
    assert g.ids["2026203"] in {s.id for s in state.unassigned}
    assert g.ids["2026211"] in {s.id for s in state.unassigned}
    assert g.apply(2026202, 3) == "막힘"
    g.assign_and_confirm()


def test_hard():
    g = Game("어려움.csv", 4)
    assert g.apply(2026301, 1) == "승인"
    assert g.apply(2026304, 1) == "대기"
    assert g.vote(2026301, 2026304) == "승인"
    assert g.apply(2026307, 1) == "대기"
    assert g.vote(2026301, 2026307) == "대기"
    assert g.vote(2026304, 2026307) == "승인"
    assert g.apply(2026302, 2) == "승인"
    assert g.apply(2026308, 2) == "대기"
    assert g.vote(2026302, 2026308) == "승인"
    assert g.apply(2026305, 2) == "대기"
    assert g.apply(2026306, 2) == "대기"
    assert g.vote(2026302, 2026305) == "대기"
    assert g.vote(2026308, 2026305) == "승인"
    assert g.vote(2026302, 2026306) == "대기"
    assert g.vote(2026308, 2026306) == "대기"
    assert g.vote(2026305, 2026306) == "거절"
    assert g.apply(2026311, 3) == "승인"
    assert g.apply(2026315, 3) == "대기"
    assert g.vote(2026311, 2026315) == "승인"
    assert g.apply(2026314, 3) == "막힘"
    assert g.apply(2026310, 4) == "승인"
    assert g.apply(2026313, 1) == "대기"
    assert g.vote(2026301, 2026313) == "대기"
    assert g.s.close().startswith("승인")
    state = g.s.snapshot()
    assert not state.pending
    assert g.ids["2026310"] in {s.id for s in state.unassigned}
    g.assign_and_confirm()


def student(i, main, *subs):
    return Student(i, str(i), f"s{i}", main, subs)


def test_one_person_cannot_fill_both_required_roles():
    both = student(1, "기획자", "웹 프로그래머")
    assert team_problems(1, [both, student(2, "테스터")]) == ["팀1에 필수 포함 역할이 모자람"]


def test_ai_answer_that_breaks_rules_falls_to_next_model():
    g = Game("쉬움.csv", 1)
    assert g.s.begin_assign() == "막힘: 모집을 먼저 마감해야 함"
    g = Game("쉬움.csv", 3)
    g.s.close()
    state = g.s.snapshot()
    good = solve(state.teams, state.unassigned)
    assert good
    calls = []

    def call(model, prompt, key):
        calls.append(model)
        if len(calls) == 1:
            return None, "120초 안에 응답이 끝나지 않음", 120.0
        if len(calls) == 2:
            return json.dumps({"assignments": [[1]]}), None, 3.0
        return json.dumps({"assignments": [{"student_no": n, "team": t} for n, t in good]}), None, 3.0

    assert g.s.begin_assign() is None
    g.s.run_assign("key", call=call, models=("a", "b", "c"))
    state = g.s.snapshot()
    assert calls == ["a", "b", "c"]
    assert state.proposal["source"] == "3차 모델(c)"
    assert [l["outcome"] for l in g.s.ai_logs()] == ["성공", "응답 형식 오류", "호출 실패"]


def test_no_solution_is_reported_without_calling_ai():
    g = Game("쉬움.csv", 4)
    g.s.close()
    assert g.s.begin_assign() is None
    called = []
    g.s.run_assign("key", call=lambda *a: called.append(a))
    state = g.s.snapshot()
    assert called == []
    assert state.settings["assign_status"].startswith("규칙을 지키는 배정이 없음")
    assert state.settings["assign_result"].startswith("막힘")
    assert state.proposal is None


def test_explain_names_team_and_role():
    teams = {1: [student(1, "기획자"), student(2, "테스터")], 2: []}
    pool = [student(3, "웹 프로그래머"), student(4, "기획자")]
    assert solve(teams, pool) is None
    assert explain_no_solution(teams, pool)


def test_check_assignment_catches_duplicates_and_missing():
    teams = {1: [student(1, "기획자"), student(2, "웹 프로그래머")]}
    pool = [student(3, "테스터"), student(4, "발표자")]
    assert check_assignment(teams, pool, [("3", 1), ("3", 1)])
    assert check_assignment(teams, pool, [("3", 1), ("4", 1)]) == []


def test_last_seat_race_is_stopped_by_db_constraint():
    g = Game("쉬움.csv", 1)
    with g.s.db.tx() as c:
        team_id = c.one("select id from tf_teams")["id"]
    a, b = g.ids["2026101"], g.ids["2026102"]
    with g.s.db.tx() as c:
        c.run("insert into tf_members (team_id, slot_no, student_id) values (%s, 1, %s)", (team_id, a))
    with pytest.raises(g.s.db.integrity_error):
        with g.s.db.tx() as c:
            c.run("insert into tf_members (team_id, slot_no, student_id) values (%s, 1, %s)", (team_id, b))


def test_wrong_password_and_hash():
    g = Game("쉬움.csv", 3)
    assert g.s.login("2026101", "wrong") is None
    assert g.s.login("2026101", "1234")["name"].startswith("학생1")
    assert g.s.login("admin", ADMIN_PASSWORD)["is_admin"]
    with g.s.db.tx() as c:
        assert c.one("select password_hash from tf_students where student_no = '2026101'")[
            "password_hash"].startswith("$2")


def test_restart_keeps_admin_and_clears_state():
    g = Game("쉬움.csv", 3)
    g.apply(2026101, 1)
    g.s.close()
    raw = (ROOT / "data" / "중간.csv").read_bytes()
    assert g.s.start(raw, 4).startswith("승인")
    state = g.s.snapshot()
    assert not state.closed and len(state.teams) == 4 and not any(state.teams.values())
    assert g.s.login("admin", ADMIN_PASSWORD)


def test_roster_format():
    assert parse_roster("student_no,name,main_role,sub_roles\n1,가,기획자,\n".encode("utf-8-sig"))[0][3] == []
    with pytest.raises(ValueError):
        parse_roster("student_no,name,main_role,sub_roles\n1,가,기획자\n".encode())
    with pytest.raises(ValueError):
        parse_roster("student_no,name,main_role,sub_roles\n1,가,요리사,\n".encode())


def test_time_limit_cuts_slow_call():
    import time
    with pytest.raises(TimeoutError):
        ai.call_with_limit(lambda: time.sleep(2), limit=0.2)
