# syntax=docker/dockerfile:1
# Official release sources; their prebuilt server/client images are no longer public.
# minio RELEASE.2025-04-22T22-12-26Z; mc RELEASE.2025-04-16T18-13-26Z.
FROM golang:1.24.2-bookworm AS build
RUN --mount=type=secret,id=proxy_ca \
    --mount=type=tmpfs,target=/tmp \
    --mount=type=tmpfs,target=/root/.cache/go-build \
    --mount=type=tmpfs,target=/go/pkg/mod \
    if [ -f /run/secrets/proxy_ca ]; then export SSL_CERT_FILE=/run/secrets/proxy_ca; fi; \
    export GOMAXPROCS=2; \
    CGO_ENABLED=0 GOBIN=/out go install -p 2 github.com/minio/minio@0d7408fc9969caf07de6a8c3a84f9fbb10a6739e && \
    CGO_ENABLED=0 GOBIN=/out go install -p 2 github.com/minio/mc@b00526b153a31b36767991a4f5ce2cced435ee8e && \
    mkdir -p /out/licenses/minio /out/licenses/mc && \
    cp /go/pkg/mod/github.com/minio/minio@v0.0.0-20250422221226-0d7408fc9969/LICENSE /out/licenses/minio/ && \
    cp /go/pkg/mod/github.com/minio/mc@v0.0.0-20250416181326-b00526b153a3/LICENSE /out/licenses/mc/
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl && rm -rf /var/lib/apt/lists/*
COPY --from=build /out/minio /out/mc /usr/local/bin/
COPY --from=build /out/licenses /licenses
ENTRYPOINT ["/usr/local/bin/minio"]
