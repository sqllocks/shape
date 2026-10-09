# Install Shape

Set up the command line for the local starter tutorials.

Status: available.

**Early access 0.9.1.** Profiling, contracts and drift are available and supported. Generation from a profile is available and is being hardened. Other surfaces are experimental unless labelled available. The 1.x promises describe future policy.

You need Python 3.11 or newer. The intended install line is below. This is an observed
installation blocker, not a successful 0.9.1 setup. Do not start the tutorials with a 0.9.0 package.

```bash
pip install "sqllocks-shape[domains]"
```

??? warning "Observed output (exit 0; resolves 0.9.0, not the required 0.9.1)"

    ```text
    WARNING: The directory '/home/agent/.cache/pip' or its parent directory is not owned or is not writable by the current user. The cache has been disabled. Check the permissions and owner of that directory. If executing pip with sudo, you should use sudo's -H flag.
    Collecting sqllocks-shape[domains]
      Downloading sqllocks_shape-0.9.0-py3-none-any.whl.metadata (2.9 kB)
    WARNING: sqllocks-shape 0.9.0 does not provide the extra 'domains'
    Collecting numpy<3,>=2.0 (from sqllocks-shape[domains])
      Downloading numpy-2.5.3-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl.metadata (6.6 kB)
    Collecting pyarrow>=14.0.1 (from sqllocks-shape[domains])
      Downloading pyarrow-25.0.1-cp312-cp312-manylinux_2_28_x86_64.whl.metadata (3.0 kB)
    Downloading numpy-2.5.3-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl (16.7 MB)
       ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 16.7/16.7 MB 9.7 MB/s eta 0:00:00
    Downloading pyarrow-25.0.1-cp312-cp312-manylinux_2_28_x86_64.whl (50.1 MB)
       ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 50.1/50.1 MB 13.3 MB/s eta 0:00:00
    Downloading sqllocks_shape-0.9.0-py3-none-any.whl (156 kB)
    Installing collected packages: pyarrow, numpy, sqllocks-shape
    Successfully installed numpy-2.5.3 pyarrow-25.0.1 sqllocks-shape-0.9.0

    [notice] A new release of pip is available: 25.0.1 -> 26.2.1
    [notice] To update, run: pip install --upgrade pip
    ```

[Owner: release maintainer — publish or confirm the 0.9.1 core and plugins on PyPI.
The docs environment resolved 0.9.0 without the domains extra; the databases 0.9.1 package
was unavailable. A successful 0.9.1 install transcript is required before this page is published.]

The tutorials are checked against the repository's 0.9.1 core and local domains and databases
plugins. That developer setup does not prove the public index serves the same packages.
The DuckDB tutorial additionally needs the databases plugin and DuckDB. Python's package
manager installs dependency packages; those downloads use the network.

## Extras

Core declares `domains` for domain schemas, `duckdb` for the local database adapter,
`postgres` and `mysql` for database drivers, and `fabric` for Fabric commands and writers.
The Snowflake and Databricks drivers are extras of `sqllocks-shape-databases`.
The [database pages](databases/index.md) explain their behavior.
Use only the extras your workflow needs. Installing a plugin gives its code the same
process permissions as core; see [What leaves my machine](WHAT_LEAVES.md).

## Related

[Start here](LEARNING_PATHS.md) · [Troubleshooting](TROUBLESHOOTING.md)
