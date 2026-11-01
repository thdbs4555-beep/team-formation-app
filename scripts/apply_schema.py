"""`schema.sql`을 DATABASE_URL의 DB에 적용한다. 주소와 비밀번호는 출력하지 않는다."""
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

root = Path(__file__).resolve().parent.parent
load_dotenv(root / ".env")

try:
    with psycopg.connect(os.environ["DATABASE_URL"], prepare_threshold=None, connect_timeout=15) as conn:
        conn.execute((root / "schema.sql").read_text(encoding="utf-8"))
        tables = conn.execute(
            "select tablename, rowsecurity from pg_tables "
            "where schemaname = 'public' and tablename like 'tf\\_%' order by tablename").fetchall()
        unique = conn.execute(
            "select conname from pg_constraint where conrelid = 'tf_members'::regclass "
            "and contype in ('p', 'u') order by conname").fetchall()
except psycopg.Error as error:
    # 예외 문구에 접속 정보가 섞일 수 있어 종류와 코드만 보여 준다.
    print("실패:", type(error).__name__, getattr(error, "sqlstate", None))
    sys.exit(1)

for name, secured in tables:
    print(name, "RLS 켜짐" if secured else "RLS 꺼짐")
print("tf_members 제약:", [row[0] for row in unique])
