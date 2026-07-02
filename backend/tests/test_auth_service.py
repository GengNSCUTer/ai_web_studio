from __future__ import annotations

import unittest

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import *  # noqa: F403 - ensure all metadata is registered.
from app.repositories.user_repo import UserRepository
from app.schemas.auth import UserLoginRequest, UserRegisterRequest
from app.services.auth_service import AuthService


class AuthServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        self.db = self.SessionLocal()
        self.service = AuthService(UserRepository(self.db))

    def tearDown(self) -> None:
        self.db.close()

    def test_register_rejects_invalid_email_format(self) -> None:
        with self.assertRaises(ValidationError):
            UserRegisterRequest(username="tester", email="not-an-email", password="password123")

    def test_register_creates_user_and_commits_transaction(self) -> None:
        response = self.service.register_user(
            UserRegisterRequest(username="tester", email="tester@example.com", password="password123")
        )

        self.assertEqual(response.token_type, "bearer")
        self.assertTrue(response.access_token)
        self.assertEqual(response.user.email, "tester@example.com")
        saved = UserRepository(self.db).get_by_email("TESTER@example.com")
        self.assertIsNotNone(saved)
        self.assertNotEqual(saved.password_hash, "password123")

    def test_register_conflict_on_duplicate_email_or_username(self) -> None:
        self.service.register_user(
            UserRegisterRequest(username="tester", email="tester@example.com", password="password123")
        )

        with self.assertRaises(HTTPException) as exc:
            self.service.register_user(
                UserRegisterRequest(username="other", email="TESTER@example.com", password="password123")
            )

        self.assertEqual(exc.exception.status_code, 409)

    def test_login_success_and_invalid_password(self) -> None:
        self.service.register_user(
            UserRegisterRequest(username="tester", email="tester@example.com", password="password123")
        )

        response = self.service.login_user(
            UserLoginRequest(email="tester@example.com", password="password123")
        )
        self.assertTrue(response.access_token)

        with self.assertRaises(HTTPException) as exc:
            self.service.login_user(
                UserLoginRequest(email="tester@example.com", password="wrongpass123")
            )
        self.assertEqual(exc.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
