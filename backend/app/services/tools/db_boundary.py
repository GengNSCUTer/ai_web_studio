"""工具网络等待前的只读事务边界；不能顺便提交调用方未保存的业务修改。"""

from sqlalchemy.orm import Session


def release_read_transaction(db: Session | None) -> None:
    if db is None or not db.in_transaction():
        return
    if db.new or db.dirty or db.deleted:
        raise RuntimeError("工具网络调用前仍有未保存的业务修改，请先显式完成业务事务")
    db.commit()
