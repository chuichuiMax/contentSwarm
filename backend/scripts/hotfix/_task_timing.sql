\set tid 'ct_d765b0e0bce64f49b93715672f95e679'

\echo '=== content_tasks ==='
SELECT id, name, status, current_stage, mode, content_type_code, workflow_version_id,
       latest_run_id, created_at, updated_at
FROM content_tasks WHERE id = :'tid';

\echo '=== agent_runs (thread_id = task) ==='
SELECT id, status, agent_id, started_at, finished_at, created_at, updated_at,
       EXTRACT(EPOCH FROM (COALESCE(finished_at, updated_at) - COALESCE(started_at, created_at))) AS duration_sec
FROM agent_runs
WHERE thread_id = :'tid' OR id = (SELECT latest_run_id FROM content_tasks WHERE id = :'tid')
ORDER BY created_at;

\echo '=== content_node_runs ==='
SELECT id, agent_run_id, node_id, node_type, status, started_at, finished_at,
       EXTRACT(EPOCH FROM (finished_at - started_at)) AS duration_sec
FROM content_node_runs WHERE task_id = :'tid' ORDER BY started_at NULLS LAST;

\echo '=== content_artifacts ==='
SELECT id, status, created_at, updated_at FROM content_artifacts WHERE task_id = :'tid';

\echo '=== content_cover_jobs ==='
SELECT id, status, job_type, created_at, updated_at, started_at, finished_at,
       EXTRACT(EPOCH FROM (finished_at - started_at)) AS duration_sec
FROM content_cover_jobs
WHERE content_task_id = :'tid'
   OR idempotency_key LIKE '%cf9a2fae%'
ORDER BY created_at;
