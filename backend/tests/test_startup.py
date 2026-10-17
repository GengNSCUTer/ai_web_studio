"""启动检查必须只读，不能创建表或补列。"""

import unittest
from unittest.mock import patch
from app.core import startup


class StartupSchemaTest(unittest.TestCase):
    def test_delegates_to_version_check_without_ddl(self):
        with patch.object(startup, "require_current_schema") as check:
            startup.ensure_runtime_schema()
            check.assert_called_once_with(startup.engine)

    def test_outdated_schema_stops_startup(self):
        with patch.object(startup, "require_current_schema", side_effect=RuntimeError("版本落后")):
            with self.assertRaisesRegex(RuntimeError, "版本落后"):
                startup.ensure_runtime_schema()
