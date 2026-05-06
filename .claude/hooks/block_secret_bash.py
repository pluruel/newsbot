"""
PreToolUse hook for Bash tool.
Blocks commands that could expose .env secrets, whether from:
  - My own reasoning going off-track
  - Prompt injection via RSS article content in the DB
  - Indirect access (python scripts, grep, env dump, etc.)
"""
import json
import re
import sys

d = json.load(sys.stdin)
cmd = d.get("command", "")

BLOCKED = [
    # --- 직접 파일 접근 (.env, .env.local 등 모든 변형) ---
    # \b 대신 lookbehind 사용 — '.' 앞에는 word boundary가 작동 안 함
    (r"(?<!\w)\.env",                   ".env 파일 참조"),

    # --- 환경변수 덤프 ---
    (r"\bprintenv\b",                   "printenv 명령"),
    (r"\benv\b\s*[\|>]",               "env 출력 파이프/리다이렉트"),
    (r"export\s+-p",                    "export -p 전체 덤프"),
    (r"\bset\b\s*[\|>]",               "set 출력 파이프"),

    # --- 시크릿 키워드 직접 참조 ---
    (r"TELEGRAM_BOT_TOKEN",             "TELEGRAM_BOT_TOKEN 직접 참조"),
    (r"TELEGRAM_CHAT_ID",               "TELEGRAM_CHAT_ID 직접 참조"),
    (r"BOT_TOKEN",                      "BOT_TOKEN 참조"),

    # --- Python을 통한 우회 (python -c "...", python script.py 모두) ---
    (r"python[0-9.]*\s.*open\s*\(",    "python open() 우회"),
    (r"python[0-9.]*\s.*\.environ",    "python environ 우회"),
    (r"python[0-9.]*\s.*dotenv",       "python dotenv 우회"),
    (r"python[0-9.]*\s.*load_dotenv",  "python load_dotenv 우회"),
    (r"python[0-9.]*\s.*getenv\s*\(",  "python getenv() 우회"),

    # --- 텍스트 처리 도구 ---
    (r"\b(grep|awk|sed|strings|od|xxd|hexdump)\b.*\.env", "텍스트 도구 .env 접근"),

    # --- 네트워크 유출 ---
    (r"\b(curl|wget|nc|ncat|netcat)\b.*TOKEN", "네트워크 토큰 유출 시도"),
    (r"\b(curl|wget|nc|ncat|netcat)\b.*TELEGRAM", "네트워크 시크릿 유출 시도"),
]

for pattern, reason in BLOCKED:
    if re.search(pattern, cmd, re.IGNORECASE):
        print(f"BLOCKED: '{reason}' — 시크릿 노출 가능한 명령이 차단되었습니다.")
        sys.exit(2)
