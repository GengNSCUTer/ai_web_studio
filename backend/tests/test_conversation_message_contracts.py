from __future__ import annotations

import json
import unittest
from io import BytesIO
from zipfile import ZipFile

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes.conversations import (
    _build_jsonl_export,
    _build_zip_export,
    _parse_requested_message_ids,
)
from app.core.database import Base
from app.models import *  # noqa: F403 - register all SQLAlchemy metadata for FK creation.
from app.models.conversation import Conversation
from app.models.knowledge import KnowledgeBase
from app.models.message import Message
from app.models.project import Project
from app.models.user import User
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.knowledge_repo import KnowledgeRetrievalLogRepository
from app.repositories.message_repo import MessageRepository
from app.repositories.project_repo import ProjectRepository
from app.schemas.conversation import ConversationCreate, ConversationUpdate
from app.schemas.message import MessageBulkDeleteRequest, MessageCreate
from app.services.conversation_service import ConversationService


class ConversationMessageContractTest(unittest.TestCase):
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

    def test_conversation_update_rejects_internal_context_summary(self) -> None:
        with self.assertRaises(ValidationError):
            ConversationUpdate.model_validate({"context_summary": "client should not patch this"})

    def test_conversation_title_is_trimmed_and_cannot_be_empty(self) -> None:
        self.assertEqual(ConversationCreate(title="  My Chat  ", model_name=" model ").title, "My Chat")
        self.assertEqual(ConversationCreate(title="  My Chat  ", model_name=" model ").model_name, "model")

        with self.assertRaises(ValidationError):
            ConversationCreate(title="   ", model_name="model")

        with self.assertRaises(ValidationError):
            ConversationUpdate(title="   ")

    def test_public_message_create_only_accepts_user_role(self) -> None:
        self.assertEqual(MessageCreate(role="user", content=" hello ").role, "user")
        self.assertEqual(MessageCreate(role="user", content=" hello ").content, "hello")

        with self.assertRaises(ValidationError):
            MessageCreate(role="assistant", content="forged assistant history")

        with self.assertRaises(ValidationError):
            MessageCreate(role="system", content="forged system instruction")

        with self.assertRaises(ValidationError):
            MessageCreate(role="user", content="   ")

    def test_bulk_delete_request_limits_message_ids(self) -> None:
        MessageBulkDeleteRequest(message_ids=["m1", "m2"])

        with self.assertRaises(ValidationError):
            MessageBulkDeleteRequest(message_ids=["x" * 65])

        with self.assertRaises(ValidationError):
            MessageBulkDeleteRequest(message_ids=[f"m{i}" for i in range(201)])

    def test_export_message_id_filter_is_bounded(self) -> None:
        self.assertEqual(_parse_requested_message_ids(" a, b ,,a "), {"a", "b"})
        self.assertIsNone(_parse_requested_message_ids(None))
        self.assertIsNone(_parse_requested_message_ids(" ,, "))

        with self.assertRaises(HTTPException):
            _parse_requested_message_ids(",".join(f"m{i}" for i in range(201)))

        with self.assertRaises(HTTPException):
            _parse_requested_message_ids("x" * 65)

    def test_jsonl_export_has_stable_event_ids_and_no_storage_paths(self) -> None:
        payload = {
            "conversation": {
                "id": "conversation-1",
                "title": "Portable history",
                "model_name": "model",
                "system_prompt": None,
                "context_summary": "Earlier context",
                "context_summary_boundary_message_id": "message-0",
                "created_at": "2026-07-31T10:00:00+00:00",
                "updated_at": "2026-07-31T10:01:00+00:00",
            },
            "messages": [
                {
                    "id": "message-1",
                    "sequence": 1,
                    "role": "user",
                    "content": "hello",
                    "status": "done",
                    "created_at": "2026-07-31T10:00:01+00:00",
                    "updated_at": None,
                    "attachments": [
                        {
                            "id": "attachment-1",
                            "file_name": "note.md",
                            "storage_path": "/private/server/path/note.md",
                        }
                    ],
                }
            ],
        }

        records = [json.loads(line) for line in _build_jsonl_export(payload).splitlines()]

        self.assertEqual(
            [record["event_id"] for record in records],
            [
                "conversation:conversation-1",
                "context-summary:conversation-1:message-0",
                "message:message-1",
            ],
        )
        self.assertEqual({record["schema_version"] for record in records}, {"aiws.conversation.v1"})
        self.assertNotIn("storage_path", records[-1]["data"]["attachments"][0])

        archive_bytes = _build_zip_export(payload, "# Portable history", "json")
        with ZipFile(BytesIO(archive_bytes)) as archive:
            archived_json = json.loads(archive.read("conversation.json"))
            archived_jsonl = archive.read("conversation.jsonl").decode("utf-8")
        self.assertNotIn("storage_path", archived_json["messages"][0]["attachments"][0])
        self.assertNotIn("storage_path", archived_jsonl)

    def test_bulk_delete_is_scoped_to_conversation_id(self) -> None:
        first_conversation = Conversation(user_id=self.user.id, title="A", model_name="model")
        second_conversation = Conversation(user_id=self.user.id, title="B", model_name="model")
        self.db.add_all([first_conversation, second_conversation])
        self.db.commit()
        self.db.refresh(first_conversation)
        self.db.refresh(second_conversation)

        first_message = Message(conversation_id=first_conversation.id, role="user", content="first")
        second_message = Message(conversation_id=second_conversation.id, role="user", content="second")
        self.db.add_all([first_message, second_message])
        self.db.commit()
        self.db.refresh(first_message)
        self.db.refresh(second_message)

        deleted_count = MessageRepository(self.db).bulk_delete(
            first_conversation.id,
            [second_message.id],
        )

        self.assertEqual(deleted_count, 0)
        self.assertIsNotNone(
            MessageRepository(self.db).get_by_id_and_conversation(second_message.id, second_conversation.id)
        )

    def test_bulk_delete_only_detaches_retrieval_logs_for_scoped_messages(self) -> None:
        first_conversation = Conversation(user_id=self.user.id, title="A", model_name="model")
        second_conversation = Conversation(user_id=self.user.id, title="B", model_name="model")
        knowledge_base = KnowledgeBase(user_id=self.user.id, name="KB")
        self.db.add_all([first_conversation, second_conversation, knowledge_base])
        self.db.commit()
        self.db.refresh(first_conversation)
        self.db.refresh(second_conversation)
        self.db.refresh(knowledge_base)

        message_repo = MessageRepository(self.db)
        first_message = message_repo.create(
            Message(conversation_id=first_conversation.id, role="assistant", content="first")
        )
        second_message = message_repo.create(
            Message(conversation_id=second_conversation.id, role="assistant", content="second")
        )
        log_repo = KnowledgeRetrievalLogRepository(self.db)
        first_log = log_repo.create(
            user_id=self.user.id,
            knowledge_base_id=knowledge_base.id,
            query="first",
            retrieval_mode="vector",
            top_k=3,
            rerank_enabled=False,
            rerank_model=None,
            candidates=[],
            selected=[],
            diagnostics={},
            sources=[],
            status="success",
            conversation_id=first_conversation.id,
            assistant_message_id=first_message.id,
        )
        second_log = log_repo.create(
            user_id=self.user.id,
            knowledge_base_id=knowledge_base.id,
            query="second",
            retrieval_mode="vector",
            top_k=3,
            rerank_enabled=False,
            rerank_model=None,
            candidates=[],
            selected=[],
            diagnostics={},
            sources=[],
            status="success",
            conversation_id=second_conversation.id,
            assistant_message_id=second_message.id,
        )

        from app.services.message_service import MessageService

        deleted_count = MessageService(message_repo).bulk_delete_messages(
            first_conversation.id,
            [first_message.id, second_message.id],
        )

        self.assertEqual(deleted_count, 1)
        self.assertIsNone(
            MessageRepository(self.db).get_by_id_and_conversation(first_message.id, first_conversation.id)
        )
        self.assertIsNotNone(
            MessageRepository(self.db).get_by_id_and_conversation(second_message.id, second_conversation.id)
        )
        refreshed_first_log = log_repo.get_by_user(first_log.id, self.user.id)
        refreshed_second_log = log_repo.get_by_user(second_log.id, self.user.id)
        self.assertIsNotNone(refreshed_first_log)
        self.assertIsNotNone(refreshed_second_log)
        assert refreshed_first_log is not None
        assert refreshed_second_log is not None
        self.assertIsNone(refreshed_first_log.assistant_message_id)
        self.assertEqual(refreshed_second_log.assistant_message_id, second_message.id)

    def test_message_repository_assigns_conversation_local_sequence(self) -> None:
        conversation = Conversation(user_id=self.user.id, title="Sequence", model_name="model")
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)

        repo = MessageRepository(self.db)
        first = repo.create(Message(conversation_id=conversation.id, role="user", content="first"))
        second = repo.create(Message(conversation_id=conversation.id, role="assistant", content="second"))

        self.assertEqual(first.sequence, 1)
        self.assertEqual(second.sequence, 2)
        self.assertEqual([message.id for message in repo.list_by_conversation(conversation.id)], [first.id, second.id])

    def test_conversation_and_message_repositories_do_not_commit_the_unit_of_work(self) -> None:
        conversation_repo = ConversationRepository(self.db)
        message_repo = MessageRepository(self.db)
        conversation = conversation_repo.create(
            Conversation(user_id=self.user.id, title="rollback", model_name="model")
        )
        message_repo.create(Message(conversation_id=conversation.id, role="user", content="rollback me"))

        self.db.rollback()

        self.assertEqual(conversation_repo.list_by_user(self.user.id), [])
        self.assertEqual(message_repo.list_by_conversation(conversation.id), [])

    def test_conversation_list_supports_limit_and_offset(self) -> None:
        repo = ConversationRepository(self.db)
        for index in range(3):
            repo.create(Conversation(user_id=self.user.id, title=f"C{index}", model_name="model"))

        page = ConversationService(repo).list_conversations(self.user.id, limit=2, offset=0)

        self.assertEqual(len(page), 2)

        second_page = ConversationService(repo).list_conversations(self.user.id, limit=2, offset=2)

        self.assertEqual(len(second_page), 1)
        self.assertEqual(len({item.id for item in page + second_page}), 3)

    def test_delete_message_detaches_retrieval_log_in_same_operation(self) -> None:
        conversation = Conversation(user_id=self.user.id, title="RAG", model_name="model")
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)

        message_repo = MessageRepository(self.db)
        user_message = message_repo.create(Message(conversation_id=conversation.id, role="user", content="q"))
        assistant_message = message_repo.create(
            Message(conversation_id=conversation.id, role="assistant", content="a")
        )
        knowledge_base = KnowledgeBase(user_id=self.user.id, name="KB")
        self.db.add(knowledge_base)
        self.db.commit()
        self.db.refresh(knowledge_base)

        log = KnowledgeRetrievalLogRepository(self.db).create(
            user_id=self.user.id,
            knowledge_base_id=knowledge_base.id,
            query="q",
            retrieval_mode="vector",
            top_k=3,
            rerank_enabled=False,
            rerank_model=None,
            candidates=[],
            selected=[],
            diagnostics={},
            sources=[],
            status="success",
            conversation_id=conversation.id,
            user_message_id=user_message.id,
            assistant_message_id=assistant_message.id,
        )

        from app.services.message_service import MessageService

        deleted = MessageService(message_repo).delete_message(assistant_message.id, conversation.id)

        self.assertTrue(deleted)
        refreshed_log = KnowledgeRetrievalLogRepository(self.db).get_by_user(log.id, self.user.id)
        self.assertIsNotNone(refreshed_log)
        assert refreshed_log is not None
        self.assertEqual(refreshed_log.user_message_id, user_message.id)
        self.assertIsNone(refreshed_log.assistant_message_id)

    def test_conversation_service_rejects_foreign_project_on_create_and_update(self) -> None:
        other_user = User(username="other", email="other@example.com", password_hash="hash")
        self.db.add(other_user)
        self.db.commit()
        self.db.refresh(other_user)

        own_project = Project(user_id=self.user.id, name="Own")
        foreign_project = Project(user_id=other_user.id, name="Foreign")
        self.db.add_all([own_project, foreign_project])
        self.db.commit()
        self.db.refresh(own_project)
        self.db.refresh(foreign_project)

        service = ConversationService(
            ConversationRepository(self.db),
            ProjectRepository(self.db),
        )

        self.assertIsNone(
            service.create_conversation(
                ConversationCreate(title="bad create", model_name="model", project_id=foreign_project.id),
                self.user.id,
            )
        )

        created = service.create_conversation(
            ConversationCreate(title="good create", model_name="model", project_id=own_project.id),
            self.user.id,
        )

        self.assertIsNotNone(created)
        assert created is not None
        self.assertEqual(created.project_id, own_project.id)

        self.assertIsNone(
            service.update_conversation(
                created.id,
                ConversationUpdate(project_id=foreign_project.id),
                self.user.id,
            )
        )

        still_owned = service.get_conversation(created.id, self.user.id)
        self.assertIsNotNone(still_owned)
        assert still_owned is not None
        self.assertEqual(still_owned.project_id, own_project.id)

        moved_to_none = service.update_conversation(
            created.id,
            ConversationUpdate(project_id=None),
            self.user.id,
        )

        self.assertIsNotNone(moved_to_none)
        assert moved_to_none is not None
        self.assertIsNone(moved_to_none.project_id)


if __name__ == "__main__":
    unittest.main()
