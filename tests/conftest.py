import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
DB_FILE = BACKEND / "tests" / "test.db"

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB_FILE}"
os.environ["SHEETS_WEBHOOK_URL"] = "https://sheets.test/webhook"
for key in ("FB", "TIKTOK", "SNAP"):
    os.environ[f"{key}_PIXEL_ID"] = f"{key.lower()}-pixel"
    os.environ[f"{key}_ACCESS_TOKEN"] = f"{key.lower()}-token"


@pytest.fixture(scope="session", autouse=True)
def migrated_db():
    """Run the real Alembic migration against a throwaway SQLite file."""
    DB_FILE.unlink(missing_ok=True)
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, check=True, env=os.environ.copy())
    yield
    DB_FILE.unlink(missing_ok=True)
