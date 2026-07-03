from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user_setting import UserSetting


class UserSettingRepository:
    """用户设置数据访问层：只关心 user_settings 表的查询和保存。"""

    def __init__(self, db: Session):
        self.db = db

    def get_by_user(self, user_id: str) -> UserSetting | None:
        # user_id 在模型层是 unique，因此这里最多返回一条用户设置。
        stmt = select(UserSetting).where(UserSetting.user_id == user_id).limit(1)
        return self.db.scalars(stmt).first()

    def save(self, setting: UserSetting) -> UserSetting:
        # Repository 只登记/刷新对象；commit/rollback 由 SettingService 控制事务边界。
        self.db.add(setting)
        self.db.flush()
        self.db.refresh(setting)
        return setting
