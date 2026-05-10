from app.core.database import Base, engine
from app.models import Attachment, Conversation, Message, User, UserSetting  # noqa: F401


def main() -> None:
    Base.metadata.create_all(bind=engine)
    print("database tables created")


if __name__ == "__main__":
    main()
