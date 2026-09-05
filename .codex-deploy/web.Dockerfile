ARG SOURCE_IMAGE
FROM ${SOURCE_IMAGE}

# Keep previous hashed assets for already-open browser sessions, while replacing
# the entry point and adding the newly built assets.
COPY web/dist /usr/share/nginx/html
COPY docker/nginx/default.conf /etc/nginx/conf.d/default.conf
