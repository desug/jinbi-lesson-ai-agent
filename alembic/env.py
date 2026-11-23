"""Alembic is restricted to the explicitly configured local agent test database."""
from pathlib import Path
import os
import sys
from logging.config import fileConfig

from alembic import context
from alembic.ddl.mysql import MySQLImpl
from sqlalchemy import Column, MetaData, PrimaryKeyConstraint, String, Table, create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
from app.core.database import Base
import app.models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


class TestMySQLImpl(MySQLImpl):
    # Alembic registers this implementation only in this migration process.
    __dialect__ = 'mysql'

    def version_table_impl(self, *, version_table, version_table_schema, version_table_pk, **kw):
        table = Table(version_table, MetaData(), Column('version_num', String(128), nullable=False), schema=version_table_schema)
        if version_table_pk:
            table.append_constraint(PrimaryKeyConstraint('version_num', name=f'{version_table}_pkc'))
        return table


def run_migrations_online():
    password = os.environ.get('JINBI_TEST_MYSQL_PASSWORD')
    if not password:
        raise RuntimeError('必须设置独立测试数据库密码 JINBI_TEST_MYSQL_PASSWORD')
    url = URL.create('mysql+pymysql', username='jinbi_test_user', password=password,
                     host='127.0.0.1', port=3306, database='jinbi_agent_test', query={'charset': 'utf8mb4'})
    engine = create_engine(url, poolclass=NullPool, hide_parameters=True, connect_args={'connect_timeout': 5})
    try:
        with engine.connect() as connection:
            identity = connection.execute(text('SELECT DATABASE(), CURRENT_USER(), @@port')).one()
            if tuple(identity) != ('jinbi_agent_test', 'jinbi_test_user@127.0.0.1', 3306):
                raise RuntimeError('数据库身份校验失败，已停止迁移')
            # End SQLAlchemy's implicit transaction from the identity SELECT.
            connection.commit()
            context.configure(connection=connection, target_metadata=Base.metadata,
                              compare_type=True, compare_server_default=True)
            with context.begin_transaction():
                context.run_migrations()
            # MySQL DDL commits implicitly; persist the final version update too.
            connection.commit()
    finally:
        engine.dispose()


if context.is_offline_mode():
    raise RuntimeError('禁止离线迁移：必须先验证实际数据库身份')
else:
    run_migrations_online()
