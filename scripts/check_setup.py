"""`.env` 값의 형식만 검사한다. 값 자체는 어떤 경우에도 출력하지 않는다."""
import re
import sys
from pathlib import Path

from dotenv import dotenv_values

values = dotenv_values(Path(__file__).resolve().parent.parent / ".env")
key = values.get("OPENROUTER_API_KEY") or ""
url = values.get("DATABASE_URL") or ""
admin = values.get("ADMIN_PASSWORD") or ""
match = re.fullmatch(r"(\w+)://([^:@/]+):(.*)@([^@/:]+):(\d+)/(\w+)(\?.*)?", url)
password = match.group(3) if match else ""
host = match.group(4) if match else ""
port = int(match.group(5)) if match else None

checks = {
    "OpenRouter 키 있음": bool(key),
    "키 앞부분 형식": key.startswith("sk-or-v1-"),
    "키에 공백 없음": key == key.strip() and " " not in key,
    "DB 주소가 postgresql 형식": bool(match) and match.group(1) in ("postgresql", "postgres"),
    "대괄호가 남아 있지 않음": "[" not in url and "]" not in url,
    "DB 비밀번호 칸을 채움": bool(password) and "YOUR-PASSWORD" not in url,
    "DB 비밀번호에 @ / : # ? 없음": not re.search(r"[@/:#?]", password),
    "Supabase 풀러 주소": host.endswith("pooler.supabase.com") and port in (5432, 6543),
    "관리자 비밀번호 있음": bool(admin),
    "테스트용 관리자 비밀번호가 아님": admin != "local-test-admin",
    "관리자 비밀번호가 DB 비밀번호와 다름": admin != password,
}
for name, ok in checks.items():
    print(f"{'OK ' if ok else 'NO '} {name}")
print("키 길이:", len(key), "/ DB 포트:", port,
      "/ 대괄호 위치:", "앞" if "[" in url.split("@")[0] else "", "뒤" if "[" in url.split("@")[-1] else "")
sys.exit(0 if all(checks.values()) else 1)
