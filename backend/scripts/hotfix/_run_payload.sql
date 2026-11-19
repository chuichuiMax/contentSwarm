SELECT input_payload->'model_spec' AS model_spec,
       length(input_payload->>'user_request') AS user_request_len,
       length(input_payload->>'generation_prompt') AS generation_prompt_len,
       input_payload->'creative_style'->>'name' AS creative_style,
       input_payload->'viral_source'->>'title' AS viral_title
FROM agent_runs WHERE id = 'cf9a2fae-2a31-46eb-a880-07ff2b01dc6d';
