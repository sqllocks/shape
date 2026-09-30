# Shape Fabric Environment

A Fabric **Environment** installs Shape once, so every **Spark** notebook or pipeline
run attached to it gets `import shape` with no `%pip` cell. Environments attach to
Spark notebooks and Spark job definitions only. The Python notebook
(`shape_profile.ipynb`) installs the wheel itself.

## Build steps

1. In the workspace: **New item > Environment**, name it `shape-env`.
2. **Runtime:** Home ribbon > **Runtime** > **2.0** (Spark 4.1, Python 3.13).
   Runtime 1.3 also works; the notebook detects the Spark version and uses
   `toPandas()` there (`toArrow()` on 2.0).
3. **Libraries > Custom libraries > Upload**, and choose
   `sqllocks_shape-0.9.0-py3-none-any.whl` (built by DM-03, or downloaded from
   the GitHub release / PyPI). A custom `.whl` has no platform-independence rule in
   Environments, and Shape's wheel is pure Python anyway.
4. Optional: **Libraries > External repositories / Add from YAML** with
   `environment.yml` from this folder (it is inactive by default; see its header).
   If outbound access protection is on, PyPI is blocked: upload every dependency as a
   custom wheel instead.
5. **Publish.** Two modes:

   | Mode | Time | Use for |
   |---|---|---|
   | **Quick** | about 5 seconds; the install happens at session start | the live demo, in notebooks only |
   | **Full** | 3 to 6 minutes to publish, plus 1 to 3 minutes at session start | pipelines and Spark job definitions |

   Use **Quick** for the talk. Use **Full** before running `shape_gate_spark`
   from a pipeline, because pipelines do not honour Quick-mode installs.
6. Attach `shape-env` to `shape_profile_spark` (notebook toolbar > **Environment**),
   or set it as the workspace default (Workspace settings > Data Engineering > Environment).
7. **Verify (in the workspace, on first run).** In a Spark notebook attached to the
   Environment run:

   ```python
   import numpy, pyarrow, pandas, shape

   print(numpy.__version__, pyarrow.__version__, pandas.__version__, shape.__file__)
   ```

   Required: numpy 2.x and pyarrow 14 or newer. If a version is older, uncomment
   that line in `environment.yml`, re-add the YAML and re-publish.

Nothing above was verified against a live workspace by the builder. Every step is on the
owner's dry-run checklist in `../RUNBOOK.md`.
