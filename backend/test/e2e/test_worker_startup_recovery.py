"""真实队列验证 Worker 已完成初始化并消费任务，不调用模型或修改业务数据。"""

import os
import uuid

import pytest
from arq.connections import RedisSettings, create_pool


@pytest.mark.asyncio
async def test_recovered_worker_consumes_missing_run_without_model_calls():
    redis = await create_pool(RedisSettings.from_dsn(os.environ["REDIS_URL"]))
    run_id = f"pytest-worker-recovery-{uuid.uuid4().hex}"
    try:
        job = await redis.enqueue_job("process_agent_run", run_id, _job_id=run_id)
        assert job is not None
        assert await job.result(timeout=30) is None
        result = await job.result_info()
        assert result is not None
        assert result.success
        assert result.function == "process_agent_run"
        assert result.args == (run_id,)
    finally:
        await redis.aclose()
