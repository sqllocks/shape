# Plugin Protocol — Draft 1 (superseded)

> **Superseded.** This draft describes a subprocess plugin model that is being removed. Plugins are trusted, in-process Python packages discovered through entry points; see `docs/plans/COMPLETION_PLAN.md` (D-09, §4.3).

Plugins declare protocol version and capabilities. Network, writes, subprocess/native execution, secrets and sensitive categories are deny-by-default. The reference runtime uses one JSON request and one JSON response over process stdio with bounded payloads and timeouts. Production hosts SHOULD add OS/container sandboxing, CPU/memory limits and filesystem/network namespaces. Core never trusts plugin output merely because execution succeeded.
