# Shape Fabric Environment: default installation

Use `environment.yml` as the default install path for the PySpark notebooks
`shape_profile_spark` and `shape_profile_distributed`, and the `shape_gate_spark` pipeline.
These notebooks have no inline installation cells. The Environment installs libraries on
the driver and executors. Python notebooks cannot attach an Environment; their inline
installation remains an explicit fallback (RUNBOOK section 4).

1. Create an Environment named `shape-env` in the workspace.
2. Select Runtime 2.0 (Spark 4.1, Python 3.13), or the supported Runtime 1.3.
3. In Libraries, import this folder's `environment.yml` using **Add from YAML**.
   It pins both `sqllocks-shape` and `sqllocks-shape-fabric` to the repository's release
   version, currently 0.9.4. `test_environment_pins_match_both_version_sources` in
   `plugins/shape-fabric/tests/test_native_integration.py` prevents version drift.
4. Publish in **Full** mode for pipeline use. Attach the published Environment to both
   PySpark notebooks using their Environment selector; attach the default lakehouse too.
5. Start a fresh session and verify:

   ```python
   import importlib.metadata
   import shape
   import shape_fabric

   assert shape.__version__ == "0.9.4"
   assert importlib.metadata.version("sqllocks-shape-fabric") == shape.__version__
   ```

If the release is not yet available on your package index, or outbound access protection
blocks it, upload the matching release wheels and their dependencies as custom libraries
instead, then publish and attach the Environment. Do not substitute an older release.
For semantic-model profiling also add `semantic-link-sempy>=0.8` to the Environment's
public libraries (the plugin's `semantic-link` extra).

For the Python notebook fallback, upload the matching wheels to Resources > builtin and
use the existing `%pip` cells, which install the uploaded wheels by path. The fallback
pipelines enable `_inlineInstallationEnabled = true`; inline installation is not supported
in High Concurrency or notebook reference runs. See RUNBOOK section 4 for details.

Sources: [Fabric Environment libraries](https://learn.microsoft.com/fabric/data-engineering/environment-manage-library),
[Python notebooks](https://learn.microsoft.com/fabric/data-engineering/using-python-experience-on-notebook).
Workspace installation verification is still pending; local tests validate the pins and
that the default Spark path contains no inline installation.
