# Plugin Protocol — Draft 1

Plugins declare protocol version and capabilities. Network, writes, subprocess/native execution, secrets and sensitive categories are deny-by-default. The reference runtime uses one JSON request and one JSON response over process stdio with bounded payloads and timeouts. Production hosts SHOULD add OS/container sandboxing, CPU/memory limits and filesystem/network namespaces. Core never trusts plugin output merely because execution succeeded.
