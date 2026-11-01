"""모델 3개를 한 번씩 불러 본다. 부록 3의 마감 직후 상태로 시험하며 호출 3회를 쓴다."""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
load_dotenv(root / ".env")

from teamapp import ai
from teamapp.rules import Student, check_assignment
from teamapp.service import parse_roster

roster = parse_roster((root / "data" / "어려움.csv").read_bytes())
students = {no: Student(i, no, name, main, tuple(subs))
            for i, (no, name, main, subs) in enumerate(roster)}
placed = {1: ["2026301", "2026304", "2026307"], 2: ["2026302", "2026308", "2026305"],
          3: ["2026311", "2026315"], 4: []}
teams = {no: [students[s] for s in members] for no, members in placed.items()}
taken = {s for members in placed.values() for s in members}
unassigned = [s for no, s in students.items() if no not in taken]
prompt = ai.build_prompt(teams, unassigned)
key = os.environ["OPENROUTER_API_KEY"]

for attempt, model in enumerate(sys.argv[1:] or ai.MODELS, 1):
    content, error, elapsed = ai.timed_call(model, prompt, key)
    if error:
        print(f"{attempt}차 {model}: 호출 실패 ({error}) {elapsed:.1f}초", flush=True)
        continue
    try:
        problems = check_assignment(teams, unassigned, ai.parse_pairs(content))
    except ValueError as parse_error:
        print(f"{attempt}차 {model}: 형식 오류 ({parse_error}) {elapsed:.1f}초", flush=True)
        continue
    verdict = "검사 통과" if not problems else f"검사 불합격 {problems[:3]}"
    print(f"{attempt}차 {model}: {verdict} {elapsed:.1f}초", flush=True)
