"""One short-lived connection per operation.

Sharing one connection across requests nests the second transaction as a
SAVEPOINT (psycopg `Transaction._push_savepoint`), so one operation's ROLLBACK
discards another's already-committed work. See docs/architecture.md.
"""
import os
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row


@contextmanager
def connect():
    # Read per call, not at import: application.py runs load_dotenv() after its
    # import block, so a module-level read would raise KeyError under the
    # documented local path (`cd backend && python application.py`).
    url = os.environ["DATABASE_URL"]
    with psycopg.connect(url, autocommit=True, row_factory=dict_row) as conn:
        yield conn
