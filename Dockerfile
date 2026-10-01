# syntax=docker/dockerfile:1
# Shape CLI image (PF-05): python:3.11-slim, the Shape wheel with the [azure] extra, a non-root
# user. Used by the ADF Batch Custom activity (PF-04) and anywhere a pinned runtime is wanted.
#
#   docker build -t shape .
#   docker run --rm -v "$PWD/data:/data:ro" -v "$PWD/out:/work" shape \
#       shape profile /data/d1.parquet -o /work/d1.shape --json /work/d1.summary.json
#
# There is no ENTRYPOINT: the command is the whole command line (`shape profile ...`), which is
# what a Batch Custom activity and `docker run` both pass.

# --- stage 1: build the platform wheel (with the Rust kernel) from this checkout --------------
FROM python:3.11-slim AS build
ENV RUSTUP_HOME=/opt/rustup CARGO_HOME=/opt/cargo PATH=/opt/cargo/bin:$PATH
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
    | sh -s -- -y --profile minimal --default-toolchain stable
RUN pip install --no-cache-dir "maturin>=1.15,<2"
WORKDIR /src
COPY pyproject.toml README.md LICENSE THIRD_PARTY_NOTICES.md ./
COPY rust ./rust
COPY src ./src
RUN maturin build --release --out /dist

# --- stage 2: the runtime image ----------------------------------------------------------------
FROM python:3.11-slim
LABEL org.opencontainers.image.title="shape" \
      org.opencontainers.image.description="Shape CLI: profile, check and compare how your data behaves" \
      org.opencontainers.image.source="https://github.com/sqllocks/shape" \
      org.opencontainers.image.licenses="MIT"
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin shape \
    && mkdir /work /data && chown shape:shape /work
# The wheel is bind-mounted from the build stage, so it is never a layer of its own. After the
# install: drop what a CLI never loads (Arrow Flight, headers, test suites) to stay under the
# 500 MB gate, byte-compile Shape (the `shape` user cannot write bytecode later), and fail the
# build, not the first run, if an import the CLI needs is gone.
RUN --mount=type=bind,from=build,source=/dist,target=/dist \
    pip install --no-compile "$(ls /dist/sqllocks_shape-*.whl)[azure]" \
 && site="$(python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')" \
 && rm -rf "$site"/pyarrow/include "$site"/pyarrow/tests "$site"/pyarrow/*flight* \
           "$site"/pyarrow/*.pyx "$site"/pyarrow/*.pxd "$site"/pyarrow/*.pxi \
 && find "$site" -type d -name tests -prune -exec rm -rf {} + \
 && python -m compileall -q "$site/shape" \
 && PYTHONDONTWRITEBYTECODE=1 python -c "import adlfs, azure.identity, deltalake, pyarrow.dataset, pyarrow.parquet, pyarrow.csv, shape; from shape.kernel import kernel_name; assert kernel_name() == 'rust'"
USER shape
WORKDIR /work
CMD ["shape", "--help"]
