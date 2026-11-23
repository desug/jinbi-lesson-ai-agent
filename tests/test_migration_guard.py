import os
import runpy
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import mysql

ENV = str(Path(__file__).resolve().parents[1] / 'alembic' / 'env.py')


class MigrationGuardTests(unittest.TestCase):
    def execute_env(self, identity):
        connection = MagicMock()
        connection.execute.return_value.one.return_value = identity
        engine = MagicMock()
        engine.connect.return_value.__enter__.return_value = connection
        config = MagicMock()
        config.config_file_name = None
        with patch.dict(os.environ, {'JINBI_TEST_MYSQL_PASSWORD': 'test-only'}), \
             patch('alembic.context.config', config, create=True), \
             patch('alembic.context.is_offline_mode', return_value=False, create=True), \
             patch('alembic.context.configure', create=True), \
             patch('alembic.context.begin_transaction', return_value=MagicMock(), create=True), \
             patch('alembic.context.run_migrations', create=True) as migrate, \
             patch('sqlalchemy.create_engine', return_value=engine) as factory:
            module = runpy.run_path(ENV)
            return module, migrate.call_count, factory.call_args, connection

    def test_wrong_identity_never_runs_migrations(self):
        with self.assertRaises(RuntimeError):
            self.execute_env(('other', 'jinbi_test_user@127.0.0.1', 3306))

    def test_fixed_target_and_wide_version_column(self):
        module, calls, args, conn = self.execute_env(('jinbi_agent_test', 'jinbi_test_user@127.0.0.1', 3306))
        self.assertEqual(calls, 1)
        url = args.args[0]
        self.assertEqual((url.host, url.port, url.database, url.username), ('127.0.0.1', 3306, 'jinbi_agent_test', 'jinbi_test_user'))
        impl = object.__new__(module['TestMySQLImpl'])
        table = impl.version_table_impl(version_table='alembic_version', version_table_schema=None, version_table_pk=True)
        self.assertEqual(table.c.version_num.type.length, 128)
        self.assertIn('VARCHAR(128)', str(CreateTable(table).compile(dialect=mysql.dialect())))

if __name__ == '__main__': unittest.main()
