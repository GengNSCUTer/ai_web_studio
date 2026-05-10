from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user_setting import UserSetting


class UserSettingRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_user(self, user_id: str) -> UserSetting | None:
        stmt = select(UserSetting).where(UserSetting.user_id == user_id).limit(1)
        return self.db.scalars(stmt).first()

    def save(self, setting: UserSetting) -> UserSetting:
        self.db.add(setting)
        self.db.commit()
        self.db.refresh(setting)
        return setting
