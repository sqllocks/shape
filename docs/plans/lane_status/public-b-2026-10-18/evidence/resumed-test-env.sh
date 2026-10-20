export PATH="/workspace/.onboarding/tools/bin:/workspace/.onboarding/cargo/bin:$PATH"
export RUSTUP_HOME=/workspace/.onboarding/rustup
export CARGO_HOME=/workspace/.onboarding/cargo
export CARGO_BUILD_JOBS=3
export DOCS_PROXY_IP="$(python -c 'import os,socket,urllib.parse; print(socket.gethostbyname(urllib.parse.urlsplit(os.environ["HTTP_PROXY"]).hostname))')"
export DOCS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
export SHAPE_DOCS_DBT_PACKAGES=/workspace/public-b-validation/docs-dbt-packages
export PYTEST_ADDOPTS="${PYTEST_ADDOPTS-} -o tmp_path_retention_policy=failed"
umask 022
