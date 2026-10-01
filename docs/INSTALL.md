# Install Shape

Supported Python: 3.11–3.13.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install sqllocks-shape
shape doctor
shape conformance
```

Optional streaming transports (`shape stream-profile`, `docs/plugins/streaming.md`):

```bash
pip install 'sqllocks-shape[kafka]'
pip install 'sqllocks-shape[eventhubs]'
```

For an offline/classified environment, build and approve wheels in a connected build enclave, transfer the wheelhouse and hashes through the organization's approved process, then install with `pip --no-index --find-links <wheelhouse> sqllocks-shape`. Shape does not require raw data to leave the environment.
