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
RUN --mount=type=secret,id=proxy_ca,mode=0444 \
    sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && if [ -f /run/secrets/proxy_ca ]; then \
         printf 'Acquire::https::CaInfo "/run/secrets/proxy_ca";\n' > /etc/apt/apt.conf.d/99docs-ca; \
       fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends build-essential ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* /etc/apt/apt.conf.d/99docs-ca
RUN --mount=type=secret,id=proxy_ca,mode=0444 \
    if [ -f /run/secrets/proxy_ca ]; then export CURL_CA_BUNDLE=/run/secrets/proxy_ca SSL_CERT_FILE=/run/secrets/proxy_ca; fi; \
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
    | sh -s -- -y --profile minimal --default-toolchain stable
RUN --mount=type=secret,id=proxy_ca,mode=0444 \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    pip install --no-cache-dir "maturin>=1.15,<2"
WORKDIR /src
COPY pyproject.toml README.md LICENSE THIRD_PARTY_NOTICES.md ./
COPY rust ./rust
COPY src ./src
RUN --mount=type=secret,id=proxy_ca,mode=0444 \
    if [ -f /run/secrets/proxy_ca ]; then export CARGO_HTTP_CAINFO=/run/secrets/proxy_ca; fi; \
    maturin build --release --out /dist
# Install the wheel with [azure] into a prefix here, where binutils is, and slim it before the
# runtime stage copies it: the 500 MB gate counts the uncompressed layers (what `docker images`
# reports with the classic image store), and the runtime packages alone came to 389 MB unstripped.
# - drop what a CLI never loads: Arrow Flight, headers, Cython sources, test suites;
# - strip --strip-unneeded every shared object (as Debian's dh_strip does for libraries; about
#   41 MB), except the auditwheel-grafted `*.libs` folders;
# - pip unpacks a wheel's library symlinks as copies (libarrow_python.so, .so.2500, .so.2500.1.0):
#   put the symlinks back.
# The runtime stage dlopen()s every shared object, so a damaged one fails the build.
RUN --mount=type=secret,id=proxy_ca,mode=0444 \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    pip install --no-compile --ignore-installed --prefix=/install "$(ls /dist/sqllocks_shape-*.whl)[azure]" \
 && site=/install/lib/python3.11/site-packages \
 && rm -rf "$site"/pyarrow/include "$site"/pyarrow/tests "$site"/pyarrow/*flight* \
           "$site"/pyarrow/*.pyx "$site"/pyarrow/*.pxd "$site"/pyarrow/*.pxi \
           "$site"/pyarrow/_pyarrow_cpp_tests* \
 && find "$site" -type d -name tests -prune -exec rm -rf {} + \
 && find "$site" -path '*.libs' -prune -o -type f -name '*.so*' -print0 \
    | xargs -0 strip --strip-unneeded \
 && python - "$site" <<'EOF'
import collections, hashlib, os, pathlib, sys
copies = collections.defaultdict(list)
for f in pathlib.Path(sys.argv[1]).rglob("lib*.so*"):
    if f.is_file() and not f.is_symlink():
        copies[(f.parent, hashlib.sha256(f.read_bytes()).hexdigest())].append(f)
for files in copies.values():
    keep, *dups = sorted(files, key=lambda f: len(f.name), reverse=True)
    for f in dups:
        f.unlink()
        os.symlink(keep.name, f)
EOF

# --- stage 2: the runtime image ----------------------------------------------------------------
FROM python:3.11-slim
LABEL org.opencontainers.image.title="shape" \
      org.opencontainers.image.description="Shape CLI: profile, check and compare how your data behaves" \
      org.opencontainers.image.source="https://github.com/sqllocks/shape" \
      org.opencontainers.image.licenses="MIT"
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin shape \
    && mkdir /work /data && chown shape:shape /work
COPY --from=build /install /usr/local
# Byte-compile Shape (the `shape` user cannot write bytecode later), and fail the build, not the
# first run, if a shared object no longer loads or an import the CLI needs is gone.
RUN python -m compileall -q /usr/local/lib/python3.11/site-packages/shape \
 && PYTHONDONTWRITEBYTECODE=1 python -c "import ctypes, pathlib, sysconfig; \
[ctypes.CDLL(str(p)) for p in pathlib.Path(sysconfig.get_paths()['purelib']).rglob('*.so*') if p.is_file()]" \
 && PYTHONDONTWRITEBYTECODE=1 python -c "import adlfs, aiohttp, azure.identity, cryptography.hazmat.bindings._rust, deltalake, numpy, pyarrow.compute, pyarrow.dataset, pyarrow.parquet, pyarrow.csv, pyarrow.json, shape; from shape.kernel import kernel_name; assert kernel_name() == 'rust'"
USER shape
WORKDIR /work
CMD ["shape", "--help"]
