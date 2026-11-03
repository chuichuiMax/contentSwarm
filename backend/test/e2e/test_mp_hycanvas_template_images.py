"""Live mini-program auth -> API proxy -> HyCanvas -> decodable template PNG."""

import io
import os
import uuid

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.storage.postgres.models_business import User
from yuxi.storage.postgres.models_content import ContentEmployee
from yuxi.utils.auth_utils import AuthUtils

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def template_mp_token():
    engine = create_async_engine(os.environ["POSTGRES_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:12]
    uid = f"pytest_template_{suffix}"
    employee_id = f"pytest_template_employee_{suffix}"
    phone = f"139{uuid.uuid4().int % 10**8:08d}"
    try:
        async with sessions() as db:
            db.add(
                User(
                    username=uid,
                    uid=uid,
                    phone_number=phone,
                    password_hash=AuthUtils.hash_password(uuid.uuid4().hex),
                    role="user",
                )
            )
            db.add(
                ContentEmployee(
                    id=employee_id,
                    employee_code=f"TPL{suffix}",
                    name=uid,
                    login_account=phone,
                    gender="male",
                    login_port=["app"],
                    role="user",
                    enabled=True,
                    created_by=uid,
                )
            )
            await db.commit()
        yield AuthUtils.create_access_token({"sub": employee_id, "typ": "mp", "uid": uid})
    finally:
        async with sessions() as db:
            await db.execute(delete(ContentEmployee).where(ContentEmployee.id == employee_id))
            await db.execute(delete(User).where(User.uid == uid))
            await db.commit()
        await engine.dispose()


@pytest.mark.parametrize("position", ["top-left", "top-right", "bottom-left", "bottom-right"])
@pytest.mark.parametrize("kind", ["overlay", "preview"])
async def test_system_cover_images_load_with_mp_query_token(e2e_client, template_mp_token, position, kind):
    template_id = f"system-cover-personalized-template-1-{position}"

    response = await e2e_client.get(
        f"/api/mp/content/hycanvas-templates/{template_id}/{kind}",
        params={"access_token": template_mp_token},
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("image/png")
    with Image.open(io.BytesIO(response.content)) as image:
        assert image.format == "PNG"
        assert image.size == (270, 360)
        if kind == "overlay":
            assert image.mode == "RGBA"
            assert image.getchannel("A").getextrema() == (0, 255)
        else:
            assert any(low != high for low, high in image.convert("RGB").getextrema())


async def test_template_images_require_mp_authentication(e2e_client):
    template_id = "system-cover-personalized-template-1-top-left"
    for kind in ("overlay", "preview"):
        response = await e2e_client.get(f"/api/mp/content/hycanvas-templates/{template_id}/{kind}")
        assert response.status_code == 401
        assert response.json()["detail"]["error"]["code"] == "MP_UNAUTHORIZED"
