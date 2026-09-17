"""一次性脚本：为集成测试创建/重置一个临时管理员账号，打印密码到 stdout。

用法：
  docker exec api-dev python scripts/_bootstrap_test_admin.py          # 创建或重置密码
  docker exec api-dev python scripts/_bootstrap_test_admin.py --delete # 测试结束后删除
"""

import secrets
import sys

import anyio
from sqlalchemy import select
from yuxi.repositories.department_repository import DepartmentRepository
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User
from yuxi.utils.auth_utils import AuthUtils

TEST_UID = "pytest_dj_admin"


async def main():
    try:
        pg_manager.initialize()
        async with pg_manager.get_async_session_context() as session:
            user = (await session.execute(select(User).where(User.uid == TEST_UID))).scalar_one_or_none()
            if "--delete" in sys.argv:
                if user is not None:
                    await session.delete(user)
                print("DELETED")
                return
            departments = await DepartmentRepository().list_departments()
            dept_id = departments[0].id if departments else None
            password = f"Py!{secrets.token_hex(8)}"
            if user is None:
                user = User(username=TEST_UID, uid=TEST_UID)
                session.add(user)
                action = "CREATED"
            else:
                action = "UPDATED"
            user.password_hash = AuthUtils.hash_password(password)
            user.role = "superadmin"
            user.department_id = dept_id
            user.is_deleted = 0
            user.deleted_at = None
            user.login_failed_count = 0
            user.login_locked_until = None
            print(f"{action} {password}")
    finally:
        await pg_manager.close()


anyio.run(main)
