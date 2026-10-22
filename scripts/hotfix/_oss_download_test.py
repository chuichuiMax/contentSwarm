import asyncio
import os
import sys

from sqlalchemy import text

from yuxi.storage.postgres.manager import pg_manager
from yuxi.services.material_upload_queue import material_thumb_object_name
from yuxi.storage.minio.client import get_minio_client, reset_storage_client

ITEM = sys.argv[1] if len(sys.argv) > 1 else "mli_1f0e4947f9fe42a19dcf4a846d0b3fe6"


async def main() -> None:
    reset_storage_client()
    client = get_minio_client()
    print("STORAGE_BACKEND", os.getenv("STORAGE_BACKEND"))
    print("client", type(client).__name__)
    async with pg_manager.get_async_session_context() as db:
        row = (
            await db.execute(
                text(
                    "select a.bucket_name, a.object_name, a.metadata_json->>'ingest_status' "
                    "from content_material_library_items i "
                    "join content_cover_assets a on a.id=i.asset_id "
                    "where i.id=:id limit 1"
                ),
                {"id": ITEM},
            )
        ).one_or_none()
    if row is None:
        print("no db row")
        return
    bucket, obj, ingest = row
    thumb = material_thumb_object_name(obj)
    print("ingest", ingest)
    print("object", obj)
    print("thumb", thumb)
    for name in (thumb, obj):
        try:
            data = await client.adownload_file(bucket, name)
            print("OK", name, len(data))
        except Exception as exc:
            print("FAIL", name, type(exc).__name__, exc)


asyncio.run(main())
