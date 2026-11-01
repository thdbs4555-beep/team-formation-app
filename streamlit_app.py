"""팀 편성 앱 화면. 요소의 key는 공고 2.7절의 표를 따른다."""
import os
import threading

import streamlit as st
from dotenv import load_dotenv

from teamapp.db import connect
from teamapp.service import Service, assign_running

load_dotenv()
st.set_page_config(page_title="팀 편성", page_icon="👥", layout="wide")


def secret(name):
    value = os.environ.get(name)
    if value:
        return value
    try:
        return st.secrets.get(name)
    except FileNotFoundError:
        return None


@st.cache_resource(show_spinner=False)
def get_service():
    """DB 연결 풀은 앱 전체에서 한 번만 만든다."""
    service = Service(connect(secret("DATABASE_URL")))
    service.ensure_admin(secret("ADMIN_PASSWORD"))
    return service


def show_result():
    with st.container(key="result"):
        text = st.session_state.get("result")
        if text:
            word = text.split(":")[0]
            {"승인": st.success, "대기": st.info, "거절": st.error, "막힘": st.warning}.get(word, st.write)(text)


def act(text):
    st.session_state["result"] = text
    st.rerun()


def describe(student):
    subs = f" / 보조: {', '.join(student.sub_roles)}" if student.sub_roles else ""
    return f"{student.name} ({student.main_role}{subs})"


def login_page(service):
    st.title("팀 편성")
    show_result()
    student_no = st.text_input("학번", key="login_student_no")
    password = st.text_input("비밀번호", type="password", key="login_password")
    if st.button("로그인", key="login_submit"):
        user = service.login(student_no, password)
        if user is None:
            act("막힘: 학번 또는 비밀번호가 틀림")
        st.session_state["user_no"] = user["student_no"]
        act(f"승인: {user['name']}으로 로그인함")


def team_box(service, state, no, me):
    members = state.teams[no]
    my_team = state.team_of(me["id"]) if me else None
    with st.container(key=f"team_{no}", border=True):
        st.subheader(f"팀{no}" + (" · 내 팀" if no == my_team else ""))
        for member in members:
            st.write(describe(member))
        if not members:
            st.caption("아직 팀원이 없음")
        if me is None:
            return
        cannot = my_team is not None or state.pending_of(me["id"]) is not None or state.closed
        if st.button("지원", key=f"apply_{no}", disabled=cannot):
            act(service.apply(me["id"], no))
        if no == my_team:
            vote_section(service, state, no, me)


def vote_section(service, state, no, me):
    for application in state.pending:
        if application["team_no"] != no:
            continue
        applicant = state.students[application["student_id"]]
        mine = state.votes.get(application["id"], {}).get(me["id"])
        st.write(f"지원자: {describe(applicant)}")
        left, right = st.columns(2)
        key = applicant.student_no
        if left.button("찬성", key=f"vote_yes_{key}", disabled=mine is not None):
            act(service.vote(me["id"], key, True))
        if right.button("거부", key=f"vote_no_{key}", disabled=mine is not None):
            act(service.vote(me["id"], key, False))
        if mine:
            st.caption(f"내 투표: {mine}")


def student_status(state, me):
    team = state.team_of(me["id"])
    pending = state.pending_of(me["id"])
    if state.closed and team is None:
        st.write("모집이 마감됨. 관리자의 배정을 기다림")
    elif team is not None:
        st.write(f"내 팀: 팀{team}")
    elif pending:
        st.write(f"팀{pending['team_no']} 지원의 결과를 기다리는 중")
    else:
        st.write("아직 팀이 없음")


def admin_panel(service, state):
    st.subheader("명단")
    roster = st.file_uploader("명단 CSV", type=["csv"], key="admin_roster_file")
    team_count = st.number_input("팀 수", min_value=1, max_value=20, value=3, step=1, key="admin_team_count")
    if st.button("명단으로 처음 상태 만들기", key="admin_start"):
        if roster is None:
            act("막힘: 명단 파일을 올려야 함")
        st.session_state.pop("assign_waiting", None)
        act(service.start(roster.getvalue(), int(team_count)))

    st.subheader("마감과 배정")
    if st.button("모집 마감", key="admin_close"):
        act(service.close())

    with st.container(key="unassigned", border=True):
        st.write("팀이 없는 학생")
        names = [s.name for s in state.unassigned]
        st.write(", ".join(names) if names else "없음")

    running = assign_running(state)
    if st.button("AI 배정", key="admin_assign", disabled=running or state.confirmed):
        blocked = service.begin_assign()
        if blocked:
            act(blocked)
        # 응답에 몇 분이 걸릴 수 있어 화면 갱신과 분리해 배경에서 돌린다.
        threading.Thread(target=service.run_assign, args=(secret("OPENROUTER_API_KEY"),), daemon=True).start()
        st.session_state["assign_waiting"] = True
        act("대기: AI 배정을 진행함")

    with st.container(key="assign_status", border=True):
        st.write(state.settings.get("assign_status", "AI 배정 전"))
        proposal = state.proposal
        if proposal:
            st.write(f"배정안 출처: {proposal['source']}")
            by_no = {s.student_no: s for s in state.students.values()}
            for student_no, team_no in proposal["pairs"]:
                st.write(f"팀{team_no} ← {describe(by_no[student_no])}")

    if st.button("배정안 확정", key="admin_confirm", disabled=state.proposal is None or state.confirmed):
        act(service.confirm())

    logs = service.ai_logs()
    if logs:
        with st.expander("AI 호출 기록"):
            for log in logs:
                detail = f" - {log['detail']}" if log["detail"] else ""
                st.write(f"{log['attempt']}차 `{log['model']}`: {log['outcome']} ({log['elapsed_sec']}초){detail}")


@st.fragment(run_every="5s")
def dashboard(service):
    """다른 사람의 변경이 5초 안에 보이도록 이 부분만 다시 그린다."""
    user = service.find_user(st.session_state["user_no"])
    if user is None:
        st.session_state.clear()
        st.rerun()
    state = service.snapshot()

    if st.session_state.get("assign_waiting") and "assign_result" in state.settings:
        st.session_state["result"] = state.settings["assign_result"]
        st.session_state.pop("assign_waiting")

    with st.container(key="whoami"):
        role = "관리자" if user["is_admin"] else user["main_role"]
        st.write(f"**{user['name']}** ({role})")
    if st.button("로그아웃", key="logout"):
        st.session_state.clear()
        st.rerun()
    show_result()

    me = None if user["is_admin"] else user
    if me:
        student_status(state, me)
    if user["is_admin"]:
        admin_panel(service, state)
    st.subheader("팀 목록")
    columns = st.columns(2)
    for index, no in enumerate(state.teams):
        with columns[index % 2]:
            team_box(service, state, no, me)


def main():
    missing = [n for n in ("DATABASE_URL", "ADMIN_PASSWORD") if not secret(n)]
    if missing:
        st.error(f"설정값이 없음: {', '.join(missing)}")
        return
    service = get_service()
    if "user_no" in st.session_state:
        dashboard(service)
    else:
        login_page(service)


main()
