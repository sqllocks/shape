# Plugin trust model

Plugins are **trusted, in-process Python code**. Installing a plugin is the same decision as
installing any other Python package: it runs with the permissions of the process that loads
it, it can read and write what that process can, and Shape does not sandbox, isolate or
restrict it. Shape never claims otherwise.

What Shape does, and does not, do:

- **Loading.** The host discovers plugins through Python entry points and loads a plugin only
  when it is used. A plugin that fails to import or build is contained: it is reported by
  `shape plugins doctor` and does not stop `shape profile` or any other command that does not
  use it. Containing a load failure is not a security boundary.
- **API check.** A plugin declares the plugin API version it targets; the host refuses an
  incompatible major version. This is a compatibility check, not a permission check.
- **No sandbox.** There is no subprocess runtime, no capability negotiation for plugins and
  no network, filesystem or subprocess allow-list. (The `.shape` format's "capabilities" in
  `shape.spec.capabilities` are about file-format features and are unrelated to plugins.)

What you should do:

- Install plugins only from sources you trust, pin their versions, and review them as you
  would any dependency.
- Where you need confinement, apply it to the whole process: containers, OS accounts, egress
  rules and secret scoping are deployment controls (see `docs/THREAT_MODEL.md`).
- Treat a plugin's output like any other input: Shape validates artifacts it reads
  (hashes, paths and sizes) whatever produced them, and signed artifacts can be verified with
  `shape verify`.
