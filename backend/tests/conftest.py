import os
import sys
import tempfile
from pathlib import Path

import pytest

# Point the app at a throwaway data dir before app.config is imported.
_DATA_DIR = tempfile.mkdtemp(prefix="changecast-test-")
os.environ["DATA_DIR"] = _DATA_DIR
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def fresh_db():
    from app.config import DB_FILENAME, DATA_DIR
    from app.db import connect
    from app.seed import init_db

    db_path = Path(DATA_DIR) / DB_FILENAME
    if db_path.exists():
        db_path.unlink()
    init_db()
    yield
    # Leave a clean database for the next test.
    conn = connect()
    try:
        conn.execute("DELETE FROM applied_events")
        conn.execute("DELETE FROM project_batches")
        conn.execute("DELETE FROM projection_rows")
        conn.execute("DELETE FROM stream_mode_changes")
        conn.commit()
    finally:
        conn.close()
