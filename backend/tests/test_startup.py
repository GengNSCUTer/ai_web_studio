import unittest
from unittest.mock import patch

from app.core import startup
from app.core.startup import _generation_id_migration_statements


class GenerationIdMigrationTest(unittest.TestCase):
    def test_missing_column_builds_complete_migration(self) -> None:
        statements = _generation_id_migration_statements(set(), None)

        self.assertEqual(
            statements,
            [
                "alter table messages add column generation_id varchar(36)",
                "update messages set generation_id = md5(random()::text || clock_timestamp()::text || id) where generation_id is null",
                "alter table messages alter column generation_id set default md5(random()::text || clock_timestamp()::text)",
                "alter table messages alter column generation_id set not null",
            ],
        )

    def test_nullable_partial_migration_is_completed(self) -> None:
        statements = _generation_id_migration_statements({"generation_id"}, ("YES", None))

        self.assertEqual(len(statements), 3)
        self.assertIn("where generation_id is null", statements[0])
        self.assertIn("set default", statements[1])
        self.assertIn("set not null", statements[2])

    def test_not_null_with_default_is_a_noop(self) -> None:
        self.assertEqual(
            _generation_id_migration_statements({"generation_id"}, ("NO", "md5(...)")),
            [],
        )

    def test_not_null_without_default_only_repairs_default(self) -> None:
        self.assertEqual(
            _generation_id_migration_statements({"generation_id"}, ("NO", None)),
            [
                "alter table messages alter column generation_id "
                "set default md5(random()::text || clock_timestamp()::text)"
            ],
        )


class RuntimeSchemaLockTest(unittest.TestCase):
    def test_postgres_lock_is_acquired_and_released_on_a_dedicated_connection(self) -> None:
        class FakeConnection:
            def __init__(self) -> None:
                self.statements: list[str] = []

            def __enter__(self):
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def execute(self, statement, _: dict[str, str]) -> None:
                self.statements.append(str(statement))

        class FakeEngine:
            class Dialect:
                name = "postgresql"

            dialect = Dialect()

            def __init__(self) -> None:
                self.connection = FakeConnection()

            def connect(self) -> FakeConnection:
                return self.connection

        fake_engine = FakeEngine()
        with patch.object(startup, "engine", fake_engine):
            with startup._runtime_schema_lock():
                pass

        self.assertEqual(len(fake_engine.connection.statements), 2)
        self.assertIn("pg_advisory_lock", fake_engine.connection.statements[0])
        self.assertIn("pg_advisory_unlock", fake_engine.connection.statements[1])

    def test_postgres_lock_is_released_when_schema_work_fails(self) -> None:
        class FakeConnection:
            def __init__(self) -> None:
                self.statements: list[str] = []

            def __enter__(self):
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def execute(self, statement, _: dict[str, str]) -> None:
                self.statements.append(str(statement))

        class FakeEngine:
            class Dialect:
                name = "postgresql"

            dialect = Dialect()

            def __init__(self) -> None:
                self.connection = FakeConnection()

            def connect(self) -> FakeConnection:
                return self.connection

        fake_engine = FakeEngine()
        with patch.object(startup, "engine", fake_engine):
            with self.assertRaisesRegex(RuntimeError, "schema failed"):
                with startup._runtime_schema_lock():
                    raise RuntimeError("schema failed")

        self.assertIn("pg_advisory_unlock", fake_engine.connection.statements[-1])


if __name__ == "__main__":
    unittest.main()
