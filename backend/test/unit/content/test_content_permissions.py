from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.repositories.content_repository import ContentRepository


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user", "allowed"),
    [
        (SimpleNamespace(uid="owner", role="user", department_id=1), True),
        (SimpleNamespace(uid="teammate", role="user", department_id=1), False),
        (SimpleNamespace(uid="team-admin", role="admin", department_id=1), True),
        (SimpleNamespace(uid="other-admin", role="admin", department_id=2), False),
        (SimpleNamespace(uid="platform-admin", role="superadmin", department_id=2), True),
    ],
)
async def test_task_access_is_scoped_to_owner_or_admin_tenant(user, allowed):
    task = SimpleNamespace(created_by="owner", tenant_id="1")
    repo = ContentRepository(SimpleNamespace())
    repo.get_task = AsyncMock(return_value=task)

    result = await repo.get_task_for_user("task-1", user)

    assert (result is task) is allowed
