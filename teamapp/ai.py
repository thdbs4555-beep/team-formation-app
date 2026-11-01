"""오픈라우터 호출. 모델마다 응답을 다 받을 때까지의 전체 시간을 재서 끊는다."""
import json
import re
import threading
import time

import requests

MODELS = (
    "nvidia/nemotron-3.5-lightning:free",
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
)
MODEL_LIMIT_SEC = 120
URL = "https://openrouter.ai/api/v1/chat/completions"


def build_prompt(teams, unassigned):
    describe = lambda s: {"학번": s.student_no, "주 역할": s.main_role, "보조 역할": list(s.sub_roles)}
    payload = {
        "팀": {str(no): [describe(s) for s in members] for no, members in sorted(teams.items())},
        "팀이 없는 학생": [describe(s) for s in unassigned],
    }
    return (
        "팀이 없는 학생을 팀에 배정하라. 이미 팀에 있는 학생은 옮기지 않는다.\n"
        "규칙:\n"
        "1. 팀이 없는 학생을 빠짐없이 한 팀에만 넣는다.\n"
        "2. 모든 팀은 2~4명이다. 빈 팀이 없어야 한다.\n"
        "3. 한 팀 안에서 주 역할이 겹치면 안 된다. 보조 역할은 겹쳐도 된다.\n"
        "4. 모든 팀에는 기획자를 맡을 학생과 웹 프로그래머를 맡을 학생이 서로 다른 사람으로 있어야 한다. "
        "주 역할이나 보조 역할 어느 쪽으로 맡아도 된다. 한 학생이 두 역할을 동시에 맡을 수 없다.\n"
        f"현재 상태:\n{json.dumps(payload, ensure_ascii=False)}\n"
        '설명 없이 JSON만 답한다. 형식: {"assignments": [{"student_no": "학번", "team": 팀 번호}]}'
    )


def parse_pairs(text):
    """모델 응답에서 (학번, 팀 번호) 목록을 꺼낸다. 형식이 틀리면 ValueError."""
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("응답에 JSON이 없음")
    try:
        items = json.loads(match.group(0))["assignments"]
        return [(str(i["student_no"]), int(i["team"])) for i in items]
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise ValueError("응답의 JSON 형식이 틀림") from error


def request_model(model, prompt, api_key):
    """모델 하나를 부르고 본문을 돌려준다. 실패하면 RuntimeError."""
    response = requests.post(
        URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 6000,
            "reasoning": {"effort": "low"},
        },
        timeout=(10, MODEL_LIMIT_SEC + 5),
    )
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}")
    try:
        body = response.json()
        content = body["choices"][0]["message"].get("content")
    except (ValueError, KeyError, IndexError, TypeError) as error:
        raise RuntimeError("응답 형식이 틀림") from error
    if not content or not content.strip():
        raise RuntimeError("빈 응답")
    return content


def call_with_limit(fn, limit=MODEL_LIMIT_SEC):
    """fn을 별도 스레드에서 돌리고 limit초가 지나면 기다리지 않는다.

    requests의 timeout은 조각 사이의 대기만 재므로 응답이 천천히 오면 넘길 수 있다.
    """
    box = {}

    def target():
        try:
            box["value"] = fn()
        except Exception as error:
            box["error"] = error

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(limit)
    if worker.is_alive():
        raise TimeoutError(f"{limit}초 안에 응답이 끝나지 않음")
    if "error" in box:
        raise box["error"]
    return box["value"]


def timed_call(model, prompt, api_key):
    started = time.monotonic()
    try:
        return call_with_limit(lambda: request_model(model, prompt, api_key)), None, time.monotonic() - started
    except requests.RequestException as error:
        # 예외 문구에 요청 정보가 섞일 수 있어 종류만 남긴다.
        return None, type(error).__name__, time.monotonic() - started
    except (RuntimeError, TimeoutError) as error:
        return None, str(error), time.monotonic() - started
