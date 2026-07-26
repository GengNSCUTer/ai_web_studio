from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.database import Base
from app.models import *  # noqa: F403 - ensure all metadata is registered.
from app.models.user import User
from app.repositories.setting_repo import UserSettingRepository
from app.schemas.setting import UserSettingUpdate
from app.services.secret_service import SecretService
from app.services.setting_service import SettingService


class SettingServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        self.db = self.SessionLocal()
        self.user = User(username="tester", email="tester@example.com", password_hash="hash")
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

    def tearDown(self) -> None:
        self.db.close()

    def test_knowledge_model_keys_are_dedicated_only(self) -> None:
        service = SettingService(UserSettingRepository(self.db))
        response = service.update_user_settings(
            self.user.id,
            UserSettingUpdate(
                knowledge_embedding_api_key="embedding-key-123456",
                knowledge_rerank_api_key="rerank-key-123456",
            ),
        )

        self.assertTrue(response.knowledge_embedding_has_api_key)
        self.assertTrue(response.knowledge_rerank_has_api_key)
        self.assertFalse(hasattr(response, "knowledge_has_api_key"))
        self.assertEqual(
            service.resolve_knowledge_model_api_key(self.user.id, "embedding"),
            "embedding-key-123456",
        )
        self.assertEqual(
            service.resolve_knowledge_model_api_key(self.user.id, "rerank"),
            "rerank-key-123456",
        )

        response = service.update_user_settings(
            self.user.id,
            UserSettingUpdate(clear_knowledge_embedding_api_key=True),
        )

        self.assertFalse(response.knowledge_embedding_has_api_key)
        self.assertIsNone(service.resolve_knowledge_model_api_key(self.user.id, "embedding"))
        self.assertEqual(
            service.resolve_knowledge_model_api_key(self.user.id, "rerank"),
            "rerank-key-123456",
        )

    def test_get_or_create_recovers_from_concurrent_insert_conflict(self) -> None:
        repo = UserSettingRepository(self.db)
        service = SettingService(repo)
        original_save = repo.save
        calls = {"count": 0}

        def save_with_concurrent_insert(setting):  # noqa: ANN001
            calls["count"] += 1
            if calls["count"] == 1:
                self.db.rollback()
                other_service = SettingService(UserSettingRepository(self.db))
                other_service.update_user_settings(self.user.id, UserSettingUpdate(theme_mode="dark"))
                raise IntegrityError("insert", {}, Exception("unique conflict"))
            return original_save(setting)

        with patch.object(repo, "save", side_effect=save_with_concurrent_insert):
            response = service.get_or_create_user_settings(self.user.id)

        self.assertEqual(response.user_id, self.user.id)
        self.assertEqual(response.theme_mode, "dark")

    def test_repository_save_does_not_commit_transaction(self) -> None:
        repo = UserSettingRepository(self.db)
        setting = SettingService._build_default_setting(self.user.id)

        repo.save(setting)
        self.db.rollback()

        self.assertIsNone(repo.get_by_user(self.user.id))

    def test_max_tokens_is_bounded_by_schema_and_service(self) -> None:
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            UserSettingUpdate(max_tokens=0)
        with self.assertRaises(ValidationError):
            UserSettingUpdate(max_tokens=131073)

        service = SettingService(UserSettingRepository(self.db))
        response = service.update_user_settings(self.user.id, UserSettingUpdate(max_tokens=4096))
        self.assertEqual(response.max_tokens, 4096)

    def test_vllm_provider_has_local_openai_compatible_defaults(self) -> None:
        service = SettingService(UserSettingRepository(self.db))

        response = service.update_user_settings(
            self.user.id,
            UserSettingUpdate(provider_type="vllm", api_base_url=None),
        )

        self.assertEqual(response.provider_type, "vllm")
        self.assertEqual(response.default_model, "Qwen/Qwen3-8B")
        self.assertEqual(response.api_base_url, "http://127.0.0.1:8000/v1")
        self.assertEqual(response.model_context_window, 32768)

    def test_unknown_chat_provider_is_rejected_by_schema(self) -> None:
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            UserSettingUpdate(provider_type="typo")

    def test_switching_provider_does_not_forward_the_previous_provider_api_key(self) -> None:
        service = SettingService(UserSettingRepository(self.db))
        service.update_user_settings(
            self.user.id,
            UserSettingUpdate(provider_type="openai-compatible", api_key="cloud-secret"),
        )

        response = service.update_user_settings(
            self.user.id,
            UserSettingUpdate(provider_type="vllm", api_base_url="http://127.0.0.1:8000/v1"),
        )

        self.assertFalse(response.has_api_key)
        self.assertIsNone(service.resolve_provider_api_key(self.user.id))

    def test_secret_service_requires_dedicated_key_in_production(self) -> None:
        previous_env = settings.app_env
        previous_secret = settings.secret_encryption_key
        try:
            object.__setattr__(settings, "app_env", "production")
            object.__setattr__(settings, "secret_encryption_key", "")
            with self.assertRaises(RuntimeError):
                SecretService()
        finally:
            object.__setattr__(settings, "app_env", previous_env)
            object.__setattr__(settings, "secret_encryption_key", previous_secret)


if __name__ == "__main__":
    unittest.main()
