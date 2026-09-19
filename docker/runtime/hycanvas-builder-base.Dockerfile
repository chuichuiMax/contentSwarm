ARG WEB_BUILDER_BASE=contentswarm-web-builder:1.0.0

FROM golang:1.25-bookworm AS go
FROM ${WEB_BUILDER_BASE}

COPY --from=go /usr/local/go /usr/local/go

ENV PATH=/usr/local/go/bin:$PATH \
    GOCACHE=/cache/go-build \
    GOMODCACHE=/cache/go-mod

WORKDIR /work
