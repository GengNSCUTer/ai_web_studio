from __future__ import annotations

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.core.database import Base
from app.models.user import User
from app.models.tool_config import UserSkillInstallation
from app.services.skill_catalog import SkillCatalog, SkillCatalogError


class SkillCatalogTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.SessionLocal() as db:
            user = User(username="skill-user", email="skill@example.test")
            db.add(user)
            db.commit()
            self.user_id = user.id

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_missing_credential_is_reported_and_blocks_enable(self) -> None:
        with self.SessionLocal() as db:
            catalog = SkillCatalog()
            by_key = {
                item["skill_key"]: item
                for item in catalog.list_for_user(db=db, user_id=self.user_id)
            }
            self.assertEqual(
                by_key["research.web-brief"]["missing_tool_keys"],
                ["web.tavily.search"],
            )
            self.assertEqual(
                by_key["workspace.document-review"]["missing_tool_keys"],
                [],
            )

            with self.assertRaisesRegex(SkillCatalogError, "缺少可执行能力"):
                catalog.install_or_update(
                    db=db,
                    user_id=self.user_id,
                    skill_key="research.web-brief",
                    is_enabled=True,
                )

            installation = catalog.install_or_update(
                db=db,
                user_id=self.user_id,
                skill_key="workspace.document-review",
                is_enabled=True,
            )
            self.assertTrue(installation.is_enabled)

            disabled = catalog.install_or_update(
                db=db,
                user_id=self.user_id,
                skill_key="workspace.document-review",
                is_enabled=False,
            )
            self.assertFalse(disabled.is_enabled)
            self.assertEqual(
                db.query(UserSkillInstallation)
                .filter_by(user_id=self.user_id, skill_key="workspace.document-review")
                .count(),
                1,
            )


if __name__ == "__main__":
    unittest.main()
