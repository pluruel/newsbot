"""Load .env from the repo's parent directory.

Kept outside the project root so that the file is not visible to processes
(e.g. headless `claude -p` invocations) that only see the bind-mounted
`/app` filesystem. Inside docker, env vars are already injected via compose
`env_file:`, so this call is a no-op there; on the host it populates env
for local dev and pytest runs invoked from the repo root.
"""

from pathlib import Path

from dotenv import load_dotenv

_ENV_PATH = Path(__file__).resolve().parents[1].parent / ".env"


def load_env() -> None:
    load_dotenv(_ENV_PATH)
