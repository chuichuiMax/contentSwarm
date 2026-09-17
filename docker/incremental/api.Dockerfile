# check=skip=InvalidDefaultArgInFrom
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

RUN rm -rf /app/package /app/server
COPY backend/package /app/package
COPY backend/server /app/server
