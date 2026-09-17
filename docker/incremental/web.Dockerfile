# check=skip=InvalidDefaultArgInFrom
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

RUN rm -rf /usr/share/nginx/html/*
COPY web-dist/ /usr/share/nginx/html/
