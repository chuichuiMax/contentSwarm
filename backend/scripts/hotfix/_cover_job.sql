SELECT id, mode, status, model, created_at, started_at, completed_at,
       EXTRACT(EPOCH FROM (completed_at - started_at)) AS cover_sec,
       EXTRACT(EPOCH FROM (completed_at - created_at)) AS cover_total_sec
FROM content_cover_jobs
WHERE content_task_id = 'ct_d765b0e0bce64f49b93715672f95e679'
ORDER BY created_at;
