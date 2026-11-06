SELECT length(title) AS title_len, length(body) AS body_len,
       jsonb_array_length(topics) AS topic_count
FROM content_artifacts WHERE task_id = 'ct_d765b0e0bce64f49b93715672f95e679';
