"""
PreToolUse hook for Read tool.
Blocks any attempt to read .env* files regardless of path.
"""
import json
import sys

d = json.load(sys.stdin)
path = d.get("file_path", "")
name = path.split("/")[-1]

# .env, .env.local, .env.production 등 모든 변형 차단
if name.startswith(".env"):
    print("BLOCKED: .env 파일 읽기는 차단되어 있습니다.")
    sys.exit(2)
