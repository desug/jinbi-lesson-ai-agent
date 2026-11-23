"""Schema upgrades for the isolated local DEMO budget."""
import re
import sqlite3
from contextlib import closing
from pathlib import Path


def _table_sql(name, limit):
    return (f'CREATE TABLE {name} (id INTEGER PRIMARY KEY CHECK(id=1), '
            f'used INTEGER NOT NULL CHECK(used BETWEEN 0 AND {limit}))')


def _schema(db):
    row = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='budget'").fetchone()
    return re.sub(r'\s+', ' ', row[0].replace('"budget"', 'budget')).strip() if row else None


def _replace_table(db, limit):
    db.execute(_table_sql('budget_upgrade', limit))
    db.execute('INSERT INTO budget_upgrade (id, used) SELECT id, used FROM budget')
    db.execute('DROP TABLE budget')
    db.execute('ALTER TABLE budget_upgrade RENAME TO budget')


def prepare_budget(db, limit):
    """Initialize or upgrade a known schema inside the caller's write transaction."""
    schema = _schema(db)
    if schema is None:
        db.execute(_table_sql('budget', limit))
        db.execute('INSERT INTO budget (id, used) VALUES (1, 0)')
        return 0

    schemas = {_table_sql('budget', cap): cap for cap in (5, 7, 10, limit) if cap <= limit}
    stored_limit = schemas.get(schema)
    if stored_limit is None:
        raise sqlite3.DatabaseError('unexpected budget schema')
    rows = db.execute('SELECT id, used FROM budget').fetchall()
    if (len(rows) != 1 or rows[0][0] != 1 or type(rows[0][1]) is not int
            or not 0 <= rows[0][1] <= stored_limit):
        raise sqlite3.DatabaseError('invalid request budget')
    if stored_limit < limit:
        _replace_table(db, limit)
        if db.execute('SELECT id, used FROM budget').fetchall() != rows:
            raise sqlite3.DatabaseError('budget migration verification failed')
    return rows[0][1]


def migrate_five_to_seven(path):
    """Preserve exactly used=5; reject unexpected state, schema, or replays."""
    _migrate(path, old_limit=5, new_limit=7, expected_used=5)


def migrate_seven_to_ten(path):
    """Explicitly authorized third-round extension, retaining all seven attempts."""
    _migrate(path, old_limit=7, new_limit=10, expected_used=7)


def _migrate(path, *, old_limit, new_limit, expected_used):
    path = Path(path).resolve(strict=True)
    with closing(sqlite3.connect(path.as_uri() + '?mode=rw', uri=True,
                                 timeout=10, isolation_level=None)) as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            if _schema(db) != _table_sql('budget', old_limit):
                raise ValueError('unexpected legacy budget schema')
            if db.execute('SELECT id,used FROM budget').fetchall() != [(1, expected_used)]:
                raise ValueError('unexpected legacy budget usage')
            _replace_table(db, new_limit)
            if db.execute('SELECT id,used FROM budget').fetchall() != [(1, expected_used)]:
                raise ValueError('budget migration verification failed')
            db.commit()
        except Exception:
            db.rollback()
            raise
