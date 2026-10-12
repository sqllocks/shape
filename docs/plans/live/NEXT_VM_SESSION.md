# Next VM session runbook — Shape 0.9.1 live receipts

Prepared for the Owner's next live-test session. It is built to produce receipts that close the
documentation owner notes that are still open on `docs/owner-notes`
(`python scripts/check_docs_owners.py --release` lists 31). Every note's example command is copied
from its page; the only changes are the ones a real setup forces (host, port, account names), and
each change is named where it is made. Section A needs no product fix. Section B is **gated**: run
it only after the lead says the fixes on `fix/live-findings` are merged.

Nothing in this runbook was run against your services by the person who wrote it. The local
commands (`shape seed --dry-run`, `sql://`, `from-ddl`, local Delta, `doctor --delta-table`,
`stream-profile` on a file) were run on Linux against this checkout; their output below is the real
output, except for lines that name your host, version or timing. Output of steps that touch your
services is taken from your earlier receipts (`docs/plans/evidence/owner-notes/`) or, where no
receipt exists, is described by its line shapes and marked **shape only**.

Contents: [0 Rules](#0-rules-that-every-block-follows) · [1 HOST prep](#1-host-prep) ·
[2 VM start](#2-vm-start-and-session-block) · [A Runnable now](#section-a-runnable-now) ·
[B After the fixes](#section-b-run-only-after-the-lead-says-the-fixes-are-merged) ·
[C Coverage](#c-coverage-table) · [D Not covered](#d-not-covered) ·
[E Wrap-up](#e-copy-the-receipts-out-and-shut-down) · [Appendix: flags](#appendix-flags-checked-against-the-091-cli-source)

---

## 0. Rules that every block follows

* **HOST** = the Windows 11 PC, Administrator PowerShell 7. **VM** = `ShapeTest`, Windows
  PowerShell 5.1. Every heading says which one to use.
* **Receipts.** Each section is one pasted block of the form `& { ... } | Tee-Object -FilePath
  <receipt>`. Native output is captured with `& $shape ... 2>&1 | Out-String` (inside the `Shp`
  helper below); `Start-Transcript` is not used because it misses `shape.exe` output in 5.1. The
  helper prints `PS> shape <the exact arguments>` before each command and `exit code: <n>` after
  it; each block prints the date at start and end.
* **Never use the clipboard inside a block.** Pasting a block replaces the clipboard. Files go
  host to VM with `Copy-VMFile` (section 1.4), not by copy and paste. Where a value must be typed,
  the text says "type this line".
* **Quoting.** Host snippets that contain `$`, `?` or bash syntax are in single-quoted here-strings
  so PowerShell does not substitute anything (a past receipt showed `exit=True` because it did).
  In the VM, `${h}` is written with braces because `$h:5433` would be read as a scope.
* **Waits.** Wait for the final `... end :` date line of a block before you check anything:
  the Event Hubs and Kafka runs take minutes, and a Databricks write (not in this runbook, its note
  is already resolved) takes about two minutes. Each step that is slow says how long.
* **Expected output.** "Expected" is the full text unless marked *shape only* or *contains*.
  Elapsed times, GUIDs, run ids and generated keys vary; everything else should match.
* **Test password.** The containers use the test value `ShapeTest!2026`. It appears in these
  commands on purpose. Real secrets (account keys, tokens, connection strings) are only ever read
  from the environment variables you already set.
* **After every section:** open the receipt in Notepad, Ctrl+A, Ctrl+C, paste it into the chat,
  and tell the lead the section id. Then the next section.

The section receipts are `A1-seed.txt`, `A2-kafka-profile.txt`, `A3-eventhubs-windows.txt`,
`A4-delta-doctor.txt`, `A5-doctor-connectivity.txt`, `A6-sinks-databases.txt`,
`A7-postgres-export.txt`, `A8-mysql-export.txt`, `A9-kafka-drift.txt`, and for section B `B0-guids.txt`, `B1-adls.txt`, `B2-adls-stream.txt`,
`B3-lakehouse-delta.txt`, `B4-warehouse.txt`, `B5-profile-db.txt`, `B6-publish.txt`,
`B7-notebook-setup.txt`, `B8-semantic.txt` and `B9-scale.txt`.

---

## 1. HOST prep

### 1.1 HOST — the containers are up

```powershell
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

Expected: four rows, all `Up`: `shape-pg` (5433->5432), `shape-mysql` (3306), `shape-mssql`
(1433), `shape-kafka` (9092). If one is missing, start it the way you started it before; do not go
on.

### 1.2 HOST — databases and roles this runbook needs (safe to re-run; it resets them)

Section A writes into fresh databases so `--write-mode create` has nothing in its way. Re-running
this block resets all of them.

```powershell
$pg = @'
DROP DATABASE IF EXISTS shape WITH (FORCE);
DROP DATABASE IF EXISTS shape_stream WITH (FORCE);
DROP DATABASE IF EXISTS shape_gen WITH (FORCE);
DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'shape') THEN
  CREATE ROLE shape LOGIN PASSWORD 'ShapeTest!2026'; END IF; END $$;
ALTER ROLE shape PASSWORD 'ShapeTest!2026';
CREATE DATABASE shape OWNER shape;
CREATE DATABASE shape_stream OWNER shape;
CREATE DATABASE shape_gen OWNER shape;
'@
$pg.Replace("`r","") | docker exec -i shape-pg psql -U postgres -d postgres -v ON_ERROR_STOP=1
"exit code: $LASTEXITCODE"

$my = @'
DROP DATABASE IF EXISTS shape;
CREATE DATABASE shape;
'@
$my.Replace("`r","") | docker exec -i -e "MYSQL_PWD=ShapeTest!2026" shape-mysql mysql -uroot
"exit code: $LASTEXITCODE"

$ms = 'IF DB_ID(''test'') IS NOT NULL BEGIN ALTER DATABASE test SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE test; END; CREATE DATABASE test; IF DB_ID(''shape_sinks'') IS NOT NULL BEGIN ALTER DATABASE shape_sinks SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE shape_sinks; END; CREATE DATABASE shape_sinks;'
docker exec shape-mssql /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "ShapeTest!2026" -C -b -Q $ms
"exit code: $LASTEXITCODE"
```

Expected: PostgreSQL prints `DROP DATABASE` ×3, `DO`, `ALTER ROLE`, `CREATE DATABASE` ×3 (plus `CREATE ROLE` the first time) and
`exit code: 0`; MySQL prints nothing and `exit code: 0`; SQL Server prints nothing (or
`Changed database context`) and `exit code: 0`. If `sqlcmd` is not at that path, the SQL Server
image is older: use `/opt/mssql-tools/bin/sqlcmd` and drop `-C`.

Databases created: PostgreSQL `shape`, `shape_stream`, `shape_gen` (role `shape`); MySQL `shape`
(user `root`); SQL Server `test` and `shape_sinks`.

### 1.3 HOST — let the host copy files into the VM (once)

```powershell
Enable-VMIntegrationService -VMName ShapeTest -Name 'Guest Service Interface'
Get-VMIntegrationService -VMName ShapeTest -Name 'Guest Service Interface' | Format-List Name, Enabled, PrimaryStatusDescription
New-Item -ItemType Directory -Force -Path C:\shape-out | Out-Null
```

Expected: `Enabled : True`, `PrimaryStatusDescription : OK`. (The VM must be running for `OK`; if you
run this before section 2.1, run it again afterwards.)

### 1.4 HOST — the CA certificate MySQL needs (once)

```powershell
docker cp shape-mysql:/var/lib/mysql/ca.pem C:\shape-out\mysql-ca.pem
Get-Item C:\shape-out\mysql-ca.pem | Select-Object Name, Length
```

Expected: a file of roughly 1,100 bytes named `mysql-ca.pem`. It is copied to the VM in 2.2.

---

## 2. VM start and session block

### 2.1 HOST — restore and start the VM

```powershell
Get-VMSnapshot -VMName ShapeTest -Name "+ Fabric extras" | Restore-VMSnapshot -Confirm:$false
Start-VM -Name ShapeTest; vmconnect.exe localhost ShapeTest
```

Expected: the VM window opens at the Windows sign-in. Sign in.

### 2.2 HOST — copy the CA into the VM

Run after the VM is up (`Get-VM ShapeTest` shows `Running`):

```powershell
Copy-VMFile -VMName ShapeTest -SourcePath C:\shape-out\mysql-ca.pem -DestinationPath C:\Users\Public\shape-in\mysql-ca.pem -FileSource Host -CreateFullPath -Force
"exit code: $LASTEXITCODE"
```

Expected: no output. (`$LASTEXITCODE` may be empty for a cmdlet; an error message would be red.)

### 2.3 VM — the session block (paste once per boot; the window must stay open)

```powershell
$ErrorActionPreference = 'Continue'
$shape = "$HOME\shape-live\Scripts\shape.exe"
$rcpt  = "$HOME\receipts"; New-Item -ItemType Directory -Force -Path $rcpt | Out-Null
$h = (Get-NetRoute -DestinationPrefix 0.0.0.0/0 | Sort-Object RouteMetric | Select-Object -First 1).NextHop
$env:SHAPE_CONFIRM_REMOTE    = '1'
$env:SHAPE_POSTGRES_PASSWORD = 'ShapeTest!2026'
$env:SHAPE_MYSQL_PASSWORD    = 'ShapeTest!2026'
$env:SHAPE_MSSQL_PASSWORD    = 'ShapeTest!2026'
function Shp {
  "PS> shape " + ($args -join ' ')
  $o = & $shape @args 2>&1 | ForEach-Object { "$_" } | Out-String
  $o.TrimEnd()
  "exit code: $LASTEXITCODE"
}
function Stamp($label) { "$label : " + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz') }
function Fresh($name) {
  $d = "$HOME\run\$name"
  if (Test-Path $d) { Remove-Item -Recurse -Force $d }
  New-Item -ItemType Directory -Force -Path $d | Out-Null
  Set-Location $d
}
& {
  Stamp 'session start'
  "host (gateway): $h"
  Shp --version
  "Kafka servers: $env:SHAPE_KAFKA_SERVERS"
  foreach ($p in 5433, 3306, 1433, 9092) { "port ${p}: " + (Test-NetConnection -ComputerName $h -Port $p -InformationLevel Quiet -WarningAction SilentlyContinue) }
  "mysql CA present: " + (Test-Path C:\Users\Public\shape-in\mysql-ca.pem)
  "az account: " + (az account show --query "{tenant:tenantId,user:user.name}" -o tsv 2>&1 | Out-String).Trim()
} | Tee-Object -FilePath "$rcpt\00-session.txt"
```

Expected (names of your tenant/user vary):

```text
session start : 2026-… 
host (gateway): 172.17.128.1
PS> shape --version
shape 0.9.1
exit code: 0
Kafka servers: 172.17.128.1:9092
port 5433: True
port 3306: True
port 1433: True
port 9092: True
mysql CA present: True
az account: b7c4b147-f93f-4930-a9b0-2926f1057e77    <your user>
```

If a port says `False`, fix the container or the Windows firewall rule on the HOST before going
on. If the gateway is not the address in `SHAPE_KAFKA_SERVERS`, Kafka will still advertise the old
address: use `$env:SHAPE_KAFKA_SERVERS` for Kafka (the blocks below do) and `${h}` for the rest.

---

## Section A. Runnable now

### A1 — `shape seed`, the four commands of example 3

**Closes:** `docs/TESTING_WITH_SHAPE.md:94` (the whole example: all four commands must pass).

Adaptations (forced by the setup, nothing else is changed): the PostgreSQL host and port are the
gateway and `5433`, and the URI needs `?sslmode=disable` (the sink uses `verify-full` for a host
that is not loopback, and the container has no TLS); the SQL Server target is database `test` on
the gateway with login `sa`; `shape seed` has **no** `--sink-config`, so the only way to give the
SQL Server password on its command line is inside the URI (`%21` is `!`; Shape masks it as `***`
in its output) and `trust_server_certificate=true` goes in the query; the MySQL and `sql://`
commands are the page's own. The generation schema `schema.json` is made by `shape from-ddl` from a
two-table script, as the page says (`shape from-ddl` writes one).

```powershell
Fresh 'A1'
& {
  Stamp 'A1 start'
  @'
CREATE TABLE dbo.customer (id INT IDENTITY(1,1) PRIMARY KEY, name NVARCHAR(50) NOT NULL);
CREATE TABLE dbo.orders (id INT IDENTITY(1,1) PRIMARY KEY, customer_id INT NOT NULL REFERENCES dbo.customer(id), amount DECIMAL(10,2));
'@ | Set-Content -Encoding ascii schema.sql
  Shp from-ddl schema.sql -o schema.json
  Shp seed retail --target "postgresql://shape@${h}:5433/shape?sslmode=disable" --scale small --seed 7
  Shp seed schema.json --target "mssql://sa:ShapeTest%212026@${h}:1433/test?trust_server_certificate=true" --mode truncate
  Shp seed retail --target mysql://shape@localhost/shape --dry-run
  Shp seed retail --target sql://./seed-scripts --scale tiny
  "files in seed-scripts: " + (Get-ChildItem seed-scripts).Count
  Stamp 'A1 end'
} | Tee-Object -FilePath "$rcpt\A1-seed.txt"
```

Expected, in order:

```text
PS> shape from-ddl schema.sql -o schema.json
Shape DDL import (smart)

  Source: schema.sql
  Output: schema.json
  Tables: 2
  Relationships: 1
  Business rules: 1
  Inferences: 7

  customer: 2 columns (PK: id)
  orders: 3 columns (PK: id)

Schema written to schema.json
exit code: 0
PS> shape seed retail --target postgresql://shape@172.17.128.1:5433/shape?sslmode=disable --scale small --seed 7
  customer: 1,000 rows
  address: 1,500 rows
  product_category: 50 rows
  product: 500 rows
  promotion: 200 rows
  store: 150 rows
  order: 5,000 rows
  order_line: 12,500 rows
  return: 850 rows
seeded 21,750 rows into 9 tables
exit code: 0
PS> shape seed schema.json --target mssql://sa:ShapeTest%212026@172.17.128.1:1433/test?trust_server_certificate=true --mode truncate
  customer: 20 rows
  orders: 60 rows
seeded 80 rows into 2 tables
exit code: 0
PS> shape seed retail --target mysql://shape@localhost/shape --dry-run
seed plan: mysql://shape@localhost/shape through the mysql sink, mode create, seed 42, scale small
   1. customer: 1,000 rows
   2. address: 1,500 rows
   3. product_category: 50 rows
   4. product: 500 rows
   5. promotion: 200 rows
   6. store: 150 rows
   7. order: 5,000 rows
   8. order_line: 12,500 rows
   9. return: 850 rows
  21,750 rows in 9 tables
dry run: nothing was connected to or written
exit code: 0
PS> shape seed retail --target sql://./seed-scripts --scale tiny
  customer: 100 rows
  address: 100 rows
  product_category: 100 rows
  product: 100 rows
  promotion: 100 rows
  store: 100 rows
  order: 100 rows
  order_line: 100 rows
  return: 100 rows
seeded 900 rows into 9 tables
exit code: 0
files in seed-scripts: 9
```

(The `PS> shape seed … mssql://…` line shows the test password because the helper echoes the
arguments; it is the container test value. Delete that one line from the pasted receipt if you
prefer.)

**If the SQL Server seed fails** with a login or connection error rather than the table lines, send
the whole receipt unchanged: the SQL Server sink in 0.9.1 reads a password only from an option or
the URI, and `shape seed` passes no option, so this step is the one most likely to expose a
product gap. Do not try other forms; the lead decides.

**Verify on the HOST** (optional, 1 minute; one line each):

```powershell
'SELECT count(*) FROM "order";' | docker exec -i shape-pg psql -U postgres -d shape -t
docker exec shape-mssql /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "ShapeTest!2026" -C -d test -Q "SELECT (SELECT COUNT(*) FROM dbo.customer) AS customers, (SELECT COUNT(*) FROM dbo.orders) AS orders"
```

Expected: `5000`; and `customers 20, orders 60`.

### A2 — Kafka: profile everything a topic holds

**Closes:** the first command of `docs/plugins/streaming.md:58` (the page's example 1 has two
commands; A3 runs the second, and the note closes only when both receipts exist).

Adaptation: broker is `$env:SHAPE_KAFKA_SERVERS` (the gateway address the container advertises).
The topic `orders` is filled first with the `order` table so there is something to profile (the
container creates topics on first use, as in your Kafka receipt).

```powershell
Fresh 'A2'
& {
  Stamp 'A2 start'
  Shp emit retail --scale small --table order --max-events 5000 --sink "kafka://$env:SHAPE_KAFKA_SERVERS/orders" --fresh --json
  Shp stream-profile "kafka://$env:SHAPE_KAFKA_SERVERS/orders" -o orders.json
  "orders.json bytes: " + (Get-Item orders.json).Length
  Stamp 'A2 end'
} | Tee-Object -FilePath "$rcpt\A2-kafka-profile.txt"
```

Expected: the emit prints one JSON object that contains `"events": 5000`, `"complete": true`,
`"stopped_by": "complete"`, `"retries": 0`, `"exit_code": 0` (as in your Kafka receipt; a
`Coordinator load in progress` line on stderr right after a broker start is fine), then
`exit code: 0`. `stream-profile` prints one JSON line containing `"window": "global"`,
`"events": 5000`, `"undecodable": 0`, `"written": ["orders.json"]`, then `exit code: 0`;
`orders.json` is a few tens of KB.

### A3 — Event Hubs: tumbling windows, lateness, checkpoint and `--follow`

**Closes:** the second command of `docs/plugins/streaming.md:58` (together with A2).

Adaptation: namespace `shapetest-eh-sqlbites01`, hub `shape-test`; the sign-in is the connection
string in `SHAPE_EVENTHUBS_CONNECTION_STRING`. The hub gets 5,000 `order` events from a second
window at 50 events per second (100 seconds). Event time is each order's own date, in order, so
windows close as later events arrive; the emit running in parallel is what makes the watermark
move while `--follow` is waiting.

You need **two VM PowerShell windows**. In each, paste the session block (2.3) first.

**VM window 1 — start the followed read (leave it running):**

```powershell
Fresh 'A3'
$ns = $env:EVENTHUBS_NAMESPACE_NAME
$env:SHAPE_EXE = $shape
Stamp 'A3 follow start' | Out-File -Encoding ascii "$rcpt\A3-eventhubs-windows.txt"
$cmdline = '"%SHAPE_EXE%" stream-profile eventhubs://' + $ns + '.servicebus.windows.net/shape-test --window tumbling --size 5m --allowed-lateness 30s --windows telemetry.jsonl --checkpoint telemetry.ckpt --follow > follow-out.txt 2>&1'
cmd /c $cmdline
```

(`cmd /c` with a file redirect is deliberate: it lets Ctrl+C reach `shape.exe`, which then finishes
its profile and prints its summary into `follow-out.txt`. A PowerShell pipeline would be cut by
Ctrl+C and lose the summary.) The window shows nothing while it waits.

**VM window 2 — after window 1 has been running for about 10 seconds, emit:**

```powershell
$ns = $env:EVENTHUBS_NAMESPACE_NAME
Shp emit retail --scale small --table order --max-events 5000 --sink "eventhubs://$ns.servicebus.windows.net/shape-test" --realtime --rate 50 --fresh --yes --json
```

Expected (window 2, after about 100 seconds): the JSON report with `"events": 5000`,
`"complete": true`, `"exit_code": 0`, then `exit code: 0`.

**VM window 1 — stop it:** wait 40 more seconds after window 2 finished (so the watermark and the
30-second lateness settle), then press **Ctrl+C once** in window 1. `shape.exe` finishes its
profile, writes the checkpoint and exits; the prompt returns within a few seconds. Then, still in
window 1, build the receipt:

```powershell
& {
  "PS> (follow stopped with Ctrl+C)"
  Get-Content follow-out.txt
  "windows written: " + (Get-Content telemetry.jsonl | Measure-Object -Line).Lines
  "partial windows: " + (Select-String -Path telemetry.jsonl -Pattern '"partial": true' -SimpleMatch | Measure-Object).Count
  "checkpoint exists: " + (Test-Path telemetry.ckpt)
  "--- resume with the same command; nothing already written is written again ---"
  $before = (Get-Content telemetry.jsonl | Measure-Object -Line).Lines
  Shp stream-profile "eventhubs://$ns.servicebus.windows.net/shape-test" --window tumbling --size 5m --allowed-lateness 30s --windows telemetry.jsonl --checkpoint telemetry.ckpt --follow --idle-timeout 30
  $after = (Get-Content telemetry.jsonl | Measure-Object -Line).Lines
  "windows before resume: $before   after: $after"
  Stamp 'A3 end'
} | Tee-Object -FilePath "$rcpt\A3-eventhubs-windows.txt" -Append
```

Expected: `follow-out.txt` holds one JSON line with `"uri": "eventhubs://…/shape-test"`,
`"window": "tumbling"`, `"events": 5000`, `"windows": <a few thousand>`, `"late_events": 0`,
`"undecodable": 0`, `"written": ["telemetry.jsonl"]`. `partial windows` is 0 to a handful (the
windows Ctrl+C closed early are marked `"partial": true`). `checkpoint exists: True`. The resumed
run (the same command plus `--idle-timeout 30`, added only so it stops by itself) prints a summary
with `"events": 0` and exits with `exit code: 0` after about 30 seconds, and the window count after
is **equal to or one or two above** the count before (a restart replaces partial windows with
complete ones and writes none twice).

If Ctrl+C loses `follow-out.txt` (empty file), say so and repeat with `--idle-timeout 60` appended
to the window-1 command, with no Ctrl+C: it stops itself 60 seconds after the last event.

### A4 — a local Delta table, then `shape doctor --delta-table`

**Closes:** the fourth command of `docs/CLI.md:369` (example 5) and of
`docs/TROUBLESHOOTING.md:18`; A5 supplies the other three commands of both notes.

How a local Delta table is written (checked in `src/shape/builtins/sinks/delta.py` and the CLI):
`shape generate DOMAIN --format delta -o DIR` writes one Delta table per table at `DIR/<table>`
(the `delta` extra, `deltalake`, is installed in the VM's `[azure]` core extra). The check takes the
path of one table.

```powershell
Fresh 'A4'
& {
  Stamp 'A4 start'
  Shp generate retail --scale small --seed 7 --format delta -o .\delta_out
  Shp doctor --delta-table .\delta_out\customer
  Stamp 'A4 end'
} | Tee-Object -FilePath "$rcpt\A4-delta-doctor.txt"
```

Expected:

```text
PS> shape generate retail --scale small --seed 7 --format delta -o .\delta_out
Wrote 9 delta directories to .\delta_out: 21,750 rows in 9 tables (0.13s)
exit code: 0
PS> shape doctor --delta-table .\delta_out\customer
Shape 0.9.1
  python    3.12.10  (Windows-…)
  kernel    rust  (compiled)

Required
  OK      numpy         …
  OK      pyarrow       …

Optional
  OK      …
  (missing lines for sklearn and tzdata are normal)

Checks
  PASS  delta.limits  no deletion vectors or column mapping (delta-rs …)

Result: OK
exit code: 0
```

(The elapsed time and the package list differ; the `Checks` block and `Result: OK` do not.)
Optional second check, on the Windows time-zone line only: if `tzdata` is listed as missing and a
later step warns about time zones, `pip install tzdata` in the venv; it is not needed here.

### A5 — `shape doctor` against Fabric, Kafka and Event Hubs

**Closes (with A4):** `docs/CLI.md:369` and `docs/TROUBLESHOOTING.md:18` — each lists four
commands; this block runs the other three. Both notes ask for "additional environments", so the
receipt shows the VM's own versions in each report.

Adaptations: `WORKSPACE/ITEM` is `shape-test/shape_lh`; the Kafka broker is the container; Event
Hubs is the namespace and hub of section A3. Fabric and Event Hubs use `--auth cli` (the VM's `az`
session).

```powershell
Fresh 'A5'
& {
  Stamp 'A5 start'
  $ns = $env:EVENTHUBS_NAMESPACE_NAME
  Shp doctor --fabric onelake://shape-test/shape_lh --auth cli
  Shp doctor --broker "kafka://$env:SHAPE_KAFKA_SERVERS/orders"
  Shp doctor --broker "eventhubs://$ns.servicebus.windows.net/shape-test" --auth cli
  Stamp 'A5 end'
} | Tee-Object -FilePath "$rcpt\A5-doctor-connectivity.txt"
```

Expected — each report starts with the same `Shape 0.9.1 / python / kernel / Required / Optional`
block as A4 and ends with a `Checks` block and `Result: OK` / `exit code: 0`:

```text
Checks                                   (Fabric)
  PASS  fabric.onelake  …                 (DNS, TCP and TLS to onelake.dfs.fabric.microsoft.com:443)
  PASS  fabric.auth     signed in with --auth cli
  PASS  fabric.item     workspace and item exist (shape-test/shape_lh)

Checks                                   (Kafka)
  PASS  broker.dns   172.17.128.1 resolves to 172.17.128.1
  PASS  broker.tcp   connected to 172.17.128.1:9092 (…)
  PASS  broker.auth  the broker answered a metadata request

Checks                                   (Event Hubs)
  PASS  broker.dns   …
  PASS  broker.tcp   connected to shapetest-eh-sqlbites01.servicebus.windows.net:5671 (…)
  PASS  broker.tls   TLS to … works (TLSv1.2 or TLSv1.3)
  PASS  broker.auth  signed in for shapetest-eh-sqlbites01.servicebus.windows.net
```

(*shape only*: these check lines are from `src/shape/cli/doctor_checks.py`; the exact message tails
depend on your network.) A `WARN` line must be followed by its `Next:` line; send it as it is. A
`FAIL` exits 1 and is also a valid receipt.

### A6 — databases example of `docs/SINKS.md`: SQL Server create, PostgreSQL stream

**Closes:** `docs/SINKS.md:151` (example 2, both commands).

Adaptations: the SQL Server command writes to database `shape_sinks` on the container (the page's
`myserver.database.windows.net/mydb`), with the receipt's own options for the password and the
container's self-signed certificate; the PostgreSQL stream writes to database `shape_stream` on the
container (the page's `me@dbhost/shape`) as role `shape`, with `?sslmode=disable` and the
non-local confirmation `--yes`. Stream length: 5,000 `order` rows at 100 per second, about 50
seconds.

```powershell
Fresh 'A6'
& {
  Stamp 'A6 start'
  Shp generate retail --scale small --to "mssql://sa@${h}:1433/shape_sinks?schema=dbo" --write-mode create --yes --sink-config "sqlserver.password=env://SHAPE_MSSQL_PASSWORD" --sink-config "sqlserver.trust_server_certificate=true"
  Shp stream retail -t order --to "postgresql://shape@${h}:5433/shape_stream?sslmode=disable" --commit-rows 500 --realtime --rate 100 --yes
  Stamp 'A6 end'
} | Tee-Object -FilePath "$rcpt\A6-sinks-databases.txt"
```

Expected: the first command prints
`Wrote 21,750 rows in 9 tables to mssql://sa@172.17.128.1:1433/shape_sinks?schema=dbo (…s)` and
`exit code: 0` (your SQL Server receipt printed the same line with 5.68 s). The second prints a
final line starting `shape stream:` that reports 5,000 events delivered (*shape only*: its exact
wording after the count varies), `exit code: 0`, and the block ends with `A6 end` about 50 seconds
after `A6 start` plus the first command's time.

**Verify on the HOST (counts for the receipt):**

```powershell
'SELECT count(*) FROM "order";' | docker exec -i shape-pg psql -U postgres -d shape_stream -t
docker exec shape-mssql /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "ShapeTest!2026" -C -d shape_sinks -Q "SELECT (SELECT COUNT(*) FROM dbo.[order]) AS orders, (SELECT COUNT(*) FROM dbo.order_line) AS order_lines"
```

Expected: `5000`; and `orders 5000, order_lines 12500`.

### A7 — PostgreSQL export, profile and tiny write

**Closes:** `docs/databases/postgresql.md:46`.

The page exports `public.orders`; the retail domain has a table `order`, so the HOST first writes the
tiny retail data (the page's third command, run first only so the export has rows) and derives
`public.orders` from it. Adaptations: host, port, database `shape_gen`, `?sslmode=disable`,
`--yes`.

**VM:**

```powershell
Fresh 'A7'
& {
  Stamp 'A7 write start'
  Shp generate retail --scale tiny --to "postgresql://shape@${h}:5433/shape_gen?sslmode=disable" --write-mode create --yes
  Stamp 'A7 write end'
} | Tee-Object -FilePath "$rcpt\A7-postgres-export.txt"
```

Expected: `Wrote 900 rows in 9 tables to postgresql://shape@172.17.128.1:5433/shape_gen?sslmode=disable
(…s)` and `exit code: 0`.

**HOST — derive `orders`, run the page's `\copy` exactly, copy the file to the VM:**

```powershell
$sql = @'
CREATE TABLE public.orders AS SELECT * FROM public."order";
\copy public.orders TO 'orders.csv' WITH (FORMAT csv, HEADER true)
SELECT count(*) AS orders_rows FROM public.orders;
'@
$sql.Replace("`r","") | docker exec -i -w /tmp shape-pg psql -U shape -d shape_gen -v ON_ERROR_STOP=1
"exit code: $LASTEXITCODE"
docker cp shape-pg:/tmp/orders.csv C:\shape-out\orders-pg.csv
Copy-VMFile -VMName ShapeTest -SourcePath C:\shape-out\orders-pg.csv -DestinationPath C:\Users\Public\shape-in\orders.csv -FileSource Host -CreateFullPath -Force
```

Expected: `SELECT 100`, then `COPY 100`, then `orders_rows` `100`, `exit code: 0`. (The tiny scale is 100
rows per table.)

**VM — profile the exported file (the page's second command):**

```powershell
Copy-Item C:\Users\Public\shape-in\orders.csv .\orders.csv
& {
  Stamp 'A7 profile start'
  Get-Content .\orders.csv -TotalCount 2
  Shp profile orders.csv -o orders.shape
  Stamp 'A7 profile end'
} | Tee-Object -FilePath "$rcpt\A7-postgres-export.txt" -Append
```

Expected: the first two lines are the header (`order_id,customer_id,store_id,…`) and one data row;
`shape profile` prints its summary (rows 100) and `orders.shape` is written, `exit code: 0` (*shape
only*: the profile summary text).

### A8 — MySQL export, profile and tiny write

**Closes:** `docs/databases/mysql.md:55`.

Adaptations: the write goes to database `shape` as `root` over TLS with the container's own CA and
`VERIFY_CA` (the form that worked in your MySQL receipt; `?ssl=false` is the known defect #1 and is
not used); the tiny retail data is written first only so `orders(order_id, amount)` can be derived
from it. The page's SQL and its `printf`/`cat` lines are run exactly as written, on the HOST, inside
the container (the VM has no `printf`).

**VM:**

```powershell
Fresh 'A8'
& {
  Stamp 'A8 write start'
  Shp generate retail --scale tiny --to "mysql://root@${h}:3306/shape?ssl_ca=C:/Users/Public/shape-in/mysql-ca.pem&ssl-mode=VERIFY_CA" --write-mode create --yes
  Stamp 'A8 write end'
} | Tee-Object -FilePath "$rcpt\A8-mysql-export.txt"
```

Expected: `Wrote 900 rows in 9 tables to mysql://root@172.17.128.1:3306/shape?ssl_ca=…&ssl-mode=VERIFY_CA (…s)`,
`exit code: 0`. If the path form `C:/…` is refused, use exactly the `ssl_ca=` form of your earlier
MySQL receipt and tell the lead.

**HOST — derive `orders`, run the page's `SELECT … INTO OUTFILE`, the `printf` and `cat`:**

```powershell
docker exec shape-mysql rm -f /var/lib/mysql-files/orders.csv /var/lib/mysql-files/orders-data.csv
$sql = @'
USE shape;
DROP TABLE IF EXISTS orders;
CREATE TABLE orders AS SELECT order_id, order_total AS amount FROM `order`;
UPDATE orders SET amount = NULL WHERE order_id % 10 = 0;
SELECT order_id, COALESCE(CAST(amount AS CHAR), 'NULL') AS amount
INTO OUTFILE '/var/lib/mysql-files/orders-data.csv'
FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"' ESCAPED BY '"'
LINES TERMINATED BY '\n'
FROM orders;
SELECT COUNT(*) AS orders_rows, SUM(amount IS NULL) AS null_amounts FROM orders;
'@
$sql.Replace("`r","") | docker exec -i -e "MYSQL_PWD=ShapeTest!2026" shape-mysql mysql -uroot
"exit code: $LASTEXITCODE"
$sh = @'
cd /var/lib/mysql-files
printf 'order_id,amount\n' > orders.csv
cat orders-data.csv >> orders.csv
head -3 orders.csv; wc -l orders.csv
'@
$sh.Replace("`r","") | docker exec -i shape-mysql bash
docker cp shape-mysql:/var/lib/mysql-files/orders.csv C:\shape-out\orders-my.csv
Copy-VMFile -VMName ShapeTest -SourcePath C:\shape-out\orders-my.csv -DestinationPath C:\Users\Public\shape-in\orders-my.csv -FileSource Host -CreateFullPath -Force
```

(The page writes the OUTFILE to `orders.csv` and then calls the copy `orders-data.csv`; here the
server file is `orders-data.csv` so that the page's `printf 'order_id,amount\n' > orders.csv` and
`cat orders-data.csv >> orders.csv` run unchanged.)

Expected: `orders_rows 100` and `null_amounts 10`; `exit code: 0`; the bash part prints
`order_id,amount`, two data lines of the form `1,"345.98"` (the amount is quoted because the
page casts it to text; ids ending in 0 carry `"NULL"`) and `101 orders.csv`.

**VM — profile (the page's last command):**

```powershell
Copy-Item C:\Users\Public\shape-in\orders-my.csv .\orders.csv
& {
  Stamp 'A8 profile start'
  Get-Content .\orders.csv -TotalCount 3
  Shp profile orders.csv -o orders.shape
  Stamp 'A8 profile end'
} | Tee-Object -FilePath "$rcpt\A8-mysql-export.txt" -Append
```

Expected: header `order_id,amount`, then rows; `shape profile` writes `orders.shape`, `exit code: 0`
(*shape only*: the summary text). The `NULL` token is read as a missing amount (10 of 100).

### A9 — Kafka: emit a drift plan day by day

**Closes:** `docs/EMIT.md:370` (example 3).

Adaptations: broker is `$env:SHAPE_KAFKA_SERVERS`; `plan.json` is a 3-day plan written first (the
format is in `docs/DRIFT.md`: a null-rate ramp and a new category on the `order` table); `key.jsonl`
is the answer key the command writes. `--fresh` is added because the topic `orders` already holds
the events of A2 and the checkpoint must start over. The run takes 3 days × 60 s = **3 minutes**;
wait for `A9 end`.

```powershell
Fresh 'A9'
& {
  Stamp 'A9 start'
  @'
{"start": "2026-03-01", "days": 3, "events": [
  {"kind": "null_rate", "table": "order", "column": "promotion_id", "start": "2026-03-02", "ramp_days": 1, "to": 0.5},
  {"kind": "new_category", "table": "order", "column": "status", "start": "2026-03-03", "value": "lost", "share": 0.08}
]}
'@ | Set-Content -Encoding ascii plan.json
  Shp emit retail --drift-plan plan.json --rows customer=500 --rows order=2000 --sink "kafka://$env:SHAPE_KAFKA_SERVERS/orders" --realtime --day-seconds 60 --answer-key key.jsonl --fresh
  "key.jsonl lines: " + (Get-Content key.jsonl | Measure-Object -Line).Lines
  Get-Content key.jsonl
  Stamp 'A9 end'
} | Tee-Object -FilePath "$rcpt\A9-kafka-drift.txt"
```

Expected: the emit finishes with `exit code: 0` after about 180 seconds (the same command to a file
reports `"events": 54750`, three days of 18,250 events: 500 customers, 2,000 orders and the other
tables' small-scale rows); `key.jsonl lines: 3`, the lines being `kind: "drift"` records for event
`e1` on day `2026-03-02`, for `e1` on `2026-03-03`, and for `e2` on `2026-03-03`, each with
`"effect"` between 0 and 1. (*shape only* for the console text the command prints without `--json`.)

---

## Section B. Run only after the lead says the fixes are merged

Do not run anything below until the lead says `fix/live-findings` is merged **and** you have
re-installed Shape into the venv from the build the lead names (the lead gives the install line).
The commands are written now so that the session is one pass; where a path or an option depends on
the merged code, the line is marked **CONFIRM** and the lead replaces it.

Names used: workspace `shape-test`, Lakehouse `shape_lh` (schema-enabled), Warehouse `shape_wh`,
Eventhouse / KQL database `shape_eh`, Eventstream `shape_es`, ADLS account `shapetestadls01`,
container `shape-test`. The service principal and the CI workflow are separate from this VM
session; here everything signs in with `--auth cli` (the VM's `az` session, tenant
`b7c4b147-f93f-4930-a9b0-2926f1057e77`).

**B0 — VM, once: workspace and lakehouse GUIDs (used by B3 and B8).**

```powershell
Fresh 'B0'
& {
  Stamp 'B0 start'
  $ws = (az rest --method get --resource https://api.fabric.microsoft.com --url https://api.fabric.microsoft.com/v1/workspaces --query "value[?displayName=='shape-test'].id | [0]" -o tsv 2>&1 | Out-String).Trim()
  $lh = (az rest --method get --resource https://api.fabric.microsoft.com --url "https://api.fabric.microsoft.com/v1/workspaces/$ws/lakehouses" --query "value[?displayName=='shape_lh'].id | [0]" -o tsv 2>&1 | Out-String).Trim()
  "workspace guid: $ws"
  "lakehouse guid: $lh"
  Stamp 'B0 end'
} | Tee-Object -FilePath "$rcpt\B0-guids.txt"
```

Expected: two GUIDs. Then **type these two lines** (replace the GUIDs by the printed values; never
use the clipboard for this):

```powershell
$ws = '<workspace guid>'
$lh = '<lakehouse guid>'
```

### B1 — ADLS Gen2: read back what A wrote, by account key

**Closes:** the read half of `docs/SINKS.md:66` (example 0, first command). There is no separate
ADLS read note; this receipt is the evidence that the read defect (#2/#3) is fixed.

The write receipt of 2026-10-11 put Parquet under
`abfss://shape-test@shapetestadls01.dfs.core.windows.net/real`. The environment already holds
`AZURE_STORAGE_ACCOUNT_NAME` and `AZURE_STORAGE_ACCOUNT_KEY`. **CONFIRM** the read form with the
lead (the CLI had no key option for reads before the fix); the lines below are the two forms to try,
in this order, and the lead removes the one that does not exist:

```powershell
Fresh 'B1'
& {
  Stamp 'B1 start'
  Shp generate retail --scale small --seed 7 --to "abfss://shape-test@shapetestadls01.dfs.core.windows.net/raw" --table-format store=csv --batch-date 2026-10-02 --sink-config "abfss.account_key=env://AZURE_STORAGE_ACCOUNT_KEY" --yes
  # CONFIRM (lead): the read-back command from the merged branch, for example
  Shp profile "abfss://shape-test@shapetestadls01.dfs.core.windows.net/raw" --name raw -o raw.shape
  Stamp 'B1 end'
} | Tee-Object -FilePath "$rcpt\B1-adls.txt"
```

Expected: the write prints `Wrote 21,750 rows in 9 tables to abfss://shape-test@shapetestadls01…/raw (…s)`,
`exit code: 0`; the read prints the profile summary with the 9 tables' row counts (address 1,500,
customer 1,000, order 5,000, order_line 12,500, product 500, product_category 50, promotion 200,
return 850, store 150) and `exit code: 0`.

### B2 — ADLS Gen2 rolling stream (SINKS example 0, second command)

**Closes (with B1 and B3):** `docs/SINKS.md:66`.

Adaptation: account `shapetestadls01`, container `shape-test`, folder `live`. The stream runs 5,000
orders at 200 per second (about 25 seconds), rolling a file every 30 seconds with a checkpoint every
30 seconds.

```powershell
Fresh 'B2'
& {
  Stamp 'B2 start'
  Shp stream retail -t order --to "abfss://shape-test@shapetestadls01.dfs.core.windows.net/live" --roll-seconds 30 --realtime --rate 200 --checkpoint-seconds 30 --sink-config "abfss.account_key=env://AZURE_STORAGE_ACCOUNT_KEY" --yes
  Stamp 'B2 end'
} | Tee-Object -FilePath "$rcpt\B2-adls-stream.txt"
```

Expected: a final `shape stream:` line reporting 5,000 events, `exit code: 0` (*shape only*).
Check in the portal that `live/` holds at least one file.

### B3 — Fabric Lakehouse: Delta write with `--table-prefix`, read back via `onelake://`

**Closes (with B1, B2):** `docs/SINKS.md:66` (third command, `delta+abfss://…/Tables`). This is
defect block #4 (with the read-back failure `No files in log segment`):
`--table-prefix` was ignored and the tables landed in a schema-enabled Lakehouse as "Unidentified".

Adaptation: `ws@onelake…/lh.Lakehouse/Tables` is `shape-test@onelake…/shape_lh.Lakehouse/Tables`.
Test the prefix (`demo_`) and the page's command without it.

```powershell
Fresh 'B3'
& {
  Stamp 'B3 start'
  Shp generate retail --scale small --seed 7 --to "delta+abfss://shape-test@onelake.dfs.fabric.microsoft.com/shape_lh.Lakehouse/Tables" --table-prefix demo_ --auth cli --yes
  # CONFIRM (lead): the read-back path in a schema-enabled Lakehouse after the fix
  Shp profile "onelake://shape-test/shape_lh/Tables/demo_customer" --name demo_customer --auth cli -o demo_customer.shape
  Shp profile "onelake://shape-test/shape_lh/Tables/demo_order" --name demo_order --auth cli -o demo_order.shape
  Stamp 'B3 end'
} | Tee-Object -FilePath "$rcpt\B3-lakehouse-delta.txt"
```

Expected: `Wrote 21,750 rows in 9 tables to delta+abfss://shape-test@onelake.dfs.fabric.microsoft.com/shape_lh.Lakehouse/Tables (…s)`
(your 2026-10-11 run took 9.96 s), `exit code: 0`; the two profiles report 1,000 and 5,000 rows,
`exit code: 0`, and **no** `No files in log segment` error. In the portal the Lakehouse explorer
must list the nine `demo_*` tables as tables (not "Unidentified"); take one screenshot of the
explorer and attach it. Then run the page's command exactly as written, without the prefix, into the
same Lakehouse only if the lead says the un-prefixed form is also fixed (otherwise skip it).

### B4 — Fabric Warehouse: load with keys, staging cleaned up

**Closes:** evidence for `docs/plugins/sqlserver.md:50` (B5) and `docs/plugins/fabric-commands.md:59`
(B6, the warehouse line); defects #5 (PRIMARY KEY in `CREATE TABLE`) and #6 (staging cleanup failed
under `--auth cli`).

`FABRIC_SQL_ENDPOINT` is already set in the VM. The staging folder is below the Lakehouse's `Files`.

```powershell
Fresh 'B4'
& {
  Stamp 'B4 start'
  Shp generate retail --scale tiny --seed 7 --to "warehouse://$env:FABRIC_SQL_ENDPOINT/shape_wh" --sink-config "warehouse.staging_path=onelake://shape-test/shape_lh/Files/shape_staging" --sink-config "warehouse.schema_name=shape_test" --auth cli --write-mode create --yes
  Stamp 'B4 end'
} | Tee-Object -FilePath "$rcpt\B4-warehouse.txt"
```

Expected: `Wrote 900 rows in 9 tables to warehouse://…/shape_wh (…s)`, `exit code: 0`, no
`PRIMARY KEY` error and no cleanup error. Then open `Files/shape_staging` in the Lakehouse explorer and attach a screenshot: it must be empty
or absent (defect #6 left it behind). Then in the Warehouse's SQL query editor run
`SELECT COUNT(*) FROM shape_test.[order]` (expect 100) and copy the result.

(**CONFIRM** with the lead if `warehouse.schema_name` is not a sink-config key on the merged branch;
the earlier playbook says to fall back to `dbo` and record it.)

### B5 — `profile-db` on the Fabric sample warehouse

**Closes:** `docs/plugins/sqlserver.md:50` (example 1); defect #9 (`HourlyBucket '18:00-18:59'`
raised `ZonedTextError`).

The sample warehouse tables are `dbo.Trip`, `dbo.Geography`, `dbo.Weather` and `dbo.Date`. The page's
command is `shape profile-db --server SERVER --database shop --schema dbo --sample-rows 1000 -o
shop.shape --json shop-summary.json`; here the server is the Warehouse's SQL endpoint, the database
`shape_wh` (adaptation), and `--auth cli`.

```powershell
Fresh 'B5'
& {
  Stamp 'B5 start'
  Shp profile-db --server $env:FABRIC_SQL_ENDPOINT --database shape_wh --schema dbo --sample-rows 1000 -o shop.shape --json shop-summary.json --auth cli
  "summary file bytes: " + (Get-Item shop-summary.json).Length
  Shp inspect shop.shape
  Stamp 'B5 end'
} | Tee-Object -FilePath "$rcpt\B5-profile-db.txt"
```

Expected: `exit code: 0`; the command prints its written-file line; `shop-summary.json` exists; `shape
inspect` lists the sample tables with `sampled_rows: 1000` where the table is larger. No
`ZonedTextError`. (*shape only* for the summary wording.) The page's example also names Azure SQL;
the Fabric SQL endpoint is the receipt this session can produce.

### B6 — `shape publish` to the four targets

**Closes:** `docs/plugins/fabric-commands.md:59` (example 0, four commands).

All four lines of the page are run. Adaptations: Lakehouse `MyWorkspace/MyLakehouse` is
`shape-test/shape_lh`; the SQL database line needs a **Fabric SQL database** item in the workspace —
the workspace has none, so create one first in the portal (workspace, New item, SQL database, name
`shape_sqldb`; Settings, Connection strings, copy the ADO.NET string **without a password**), then
**type** its value into the variable below; the Warehouse line uses `shape_wh`; the Eventhouse line
uses the query-URI host `FABRIC_KQL_HOST` and database `FABRIC_KQL_DB` already set.

Type this line first (the ADO.NET string without a login; the `az` session signs in), and the
Warehouse one:

```powershell
$env:SHAPE_SQL_CONNECTION = 'Data Source=<the SQL database server from its settings>;Initial Catalog=shape_sqldb'
$env:WH = "Data Source=$env:FABRIC_SQL_ENDPOINT;Initial Catalog=shape_wh"
```

```powershell
Fresh 'B6'
& {
  Stamp 'B6 start'
  Shp publish retail -t lakehouse --base-path onelake://shape-test/shape_lh/Files --auth cli
  Shp publish retail -t sql-database --connection-string env://SHAPE_SQL_CONNECTION --auth cli
  Shp publish retail -t warehouse --connection-string env://WH --staging-path onelake://shape-test/shape_lh/Files --auth cli
  Shp publish retail -t eventhouse --connection-string "https://$env:FABRIC_KQL_HOST" --database $env:FABRIC_KQL_DB --auth cli
  Stamp 'B6 end'
} | Tee-Object -FilePath "$rcpt\B6-publish.txt"
```

Expected: four commands, each `exit code: 0`; the Lakehouse one reports a manifest at
`landing/retail/manifest/_control/run_manifest.json`; the others report their tables written
(*shape only*: the summary wording). The default scale is `small`. If you do not create the SQL
database item, the second command fails; send the receipt anyway and tell the lead: that note then
cannot close, because the page's example has four commands.

### B7 — notebook and `deploy-notebook`, `setup-fabric`

**Closes:** `docs/plugins/fabric-commands.md:95` (example 1) and `:117` (example 2).

Adaptations: the page's workspace name `Demo` is `shape-test`; `setup-fabric` reuses the existing
Lakehouse `shape_lh` if told to by name (an item that exists is reused).

```powershell
Fresh 'B7'
& {
  Stamp 'B7 start'
  Shp notebook retail --target lakehouse -o retail.ipynb
  "retail.ipynb bytes: " + (Get-Item retail.ipynb).Length
  Shp deploy-notebook retail --workspace "shape-test" --auth cli
  Shp setup-fabric --workspace "shape-test" --create-lakehouse --lakehouse-name shape_lh
  Shp setup-fabric --snippet
  Stamp 'B7 end'
} | Tee-Object -FilePath "$rcpt\B7-notebook-setup.txt"
```

Expected: `exit code: 0` for all four; `deploy-notebook` reports a Notebook item named
`Shape_retail_small` in `shape-test`; `setup-fabric` reports the Environment `shape-env` and the
Lakehouse (created or reused) and prints the libraries to add; `--snippet` prints the cell (*shape
only*: the wording). After this, delete the notebook and the `shape-env` Environment in the portal
if you do not want them kept.

### B8 — Fabric notebook steps (semantic model): `profile-model`, `semantic-model://`

**Closes:** `docs/plugins/fabric-commands.md:158` (example 4) and
`docs/plugins/cloud-sources.md:69` (example 0).

These two run **inside a Fabric notebook** (`sempy` is not available on the VM). Open the notebook
that B7 deployed (or a new one) in the `shape-test` workspace, attach it to a workspace that holds a
semantic model, and run these cells, copying each cell's output from the notebook into the receipt
file `B8-semantic.txt` in Notepad. Use the page's names for the model: the page says
`Sales/Retail` and `semantic-model://Sales/Retail/Customer` — replace `Sales` and `Retail` by the
workspace and model you have (a sample model such as the Fabric "Retail" sample), and replace
`Customer` by one of its tables.

```python
%pip install 'sqllocks-shape-fabric[semantic-link]'
```

```python
!shape profile semantic-model://<workspace>/<model>/<table> -o customer.shape
```

```python
!shape profile-model <workspace>/<model> -o retail.shape --tables <T1>,<T2> --max-rows 100000 --json
```

Expected: `exit code` is not shown by the notebook; each cell must end without a traceback, and
`customer.shape` / `retail.shape` must exist (`!ls -l *.shape`). Include `!shape --version`
and `!pip list | grep -i sqllocks` in the receipt. The Warehouse and Lakehouse items alone do not
satisfy this note: it needs a real semantic model.

### B9 — Scale: `shape jobs` and `fabric_spark`

**Closes:** `docs/SCALE.md:158` (example 3) and `:133` (example 2).

Adaptations: `WORKSPACE_GUID` and `LAKEHOUSE_GUID` are `$ws` and `$lh` from B0; the token is the
Fabric API token from the VM's `az` session; `--table-prefix demo_` is the page's. The run starts
the `shape_spark_worker` notebook in the workspace and waits; plan for 10 to 20 minutes and do not
close the window. The page's `--scale large` is used as written.

```powershell
Fresh 'B9'
$env:SHAPE_FABRIC_TOKEN = (az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv)
& {
  Stamp 'B9 start'
  Shp generate retail --scale large --scale-mode fabric_spark --fabric-workspace $ws --fabric-lakehouse $lh --table-prefix demo_
  Shp jobs list
  Stamp 'B9 jobs listed'
} | Tee-Object -FilePath "$rcpt\B9-scale.txt"
```

Then **type** the job id that `jobs list` showed for the Spark run (call it `<JOB>`), and run:

```powershell
& {
  Shp jobs status <JOB>
  Shp jobs cancel <JOB>
  Shp jobs resume <JOB>
  Shp jobs status <JOB>
  Stamp 'B9 end'
} | Tee-Object -FilePath "$rcpt\B9-scale.txt" -Append
```

Expected: the first command ends with `exit code: 0` when every table has exactly the requested
rows; `jobs list` shows the job with its status; `jobs status` asks Fabric and prints the run state.
`cancel` on a finished job and `resume` on a succeeded one may be refused with a message — that is
a valid receipt; if you want all four verbs to act, start a second run, run `jobs cancel` while it
is `running`, then `jobs resume` the cancelled job. (*shape only*: the status wording.)

---

## C. Coverage table

Notes are the 31 open on `docs/owner-notes` (`check_docs_owners.py --release`).

| Note (file:line) | Section / step | Result if this session passes |
|---|---|---|
| `docs/TESTING_WITH_SHAPE.md:94` | A1 | closes (if the SQL Server seed works; see the warning in A1) |
| `docs/plugins/streaming.md:58` | A2 (Kafka) + A3 (Event Hubs) | closes with both receipts |
| `docs/CLI.md:369` | A4 + A5 | closes with both receipts |
| `docs/TROUBLESHOOTING.md:18` | A4 + A5 | closes with both receipts |
| `docs/SINKS.md:151` | A6 | closes |
| `docs/databases/postgresql.md:46` | A7 | closes |
| `docs/databases/mysql.md:55` | A8 | closes |
| `docs/EMIT.md:370` | A9 | closes |
| `docs/SINKS.md:66` | B1 + B2 + B3 | closes after the fixes |
| `docs/plugins/fabric-commands.md:59` | B6 (needs a Fabric SQL database item) | closes after the fixes |
| `docs/plugins/fabric-commands.md:95` | B7 | closes |
| `docs/plugins/fabric-commands.md:117` | B7 | closes |
| `docs/plugins/fabric-commands.md:158` | B8 (Fabric notebook) | closes |
| `docs/plugins/cloud-sources.md:69` | B8 (Fabric notebook) | closes |
| `docs/plugins/sqlserver.md:50` | B5 | closes after the fixes |
| `docs/SCALE.md:133` | B9 | closes |
| `docs/SCALE.md:158` | B9 | closes |
| `docs/FABRIC_PLATFORM.md:127` | not covered: needs a GitHub repository connected to the workspace and a token with write access | see D |
| `docs/EMIT.md:281` | not covered: Avro with an authenticated Schema Registry, no setup | see D |
| `docs/plugins/fabric-auth.md:41` | not covered: Kerberos, no KDC or keytab | see D |
| `docs/plugins/fabric-writers.md:110` | not covered: needs an Azure Synapse dedicated pool | see D |
| `docs/TROUBLESHOOTING.md:32` | not covered: needs the repository fixture `tests/fixtures/delta_fallback/dv` and DuckDB's extension download; the VM has a PyPI install only | see D |
| `docs/VSCODE.md:25` | not covered: needs VS Code installed and `editors/vscode/shape.vsix` from the repository | see D |
| `docs/RELEASE_CHECKLIST.md:160`, `:184` | not covered: hosted GitHub Actions / release receipts, not a VM run | see D |
| `docs/EMIT.md:537`, `docs/GENERATION_ENGINE.md:434`, `docs/plugins/sqlserver.md:113` | not covered: committed machine-labelled performance measurements | see D |
| `THIRD_PARTY_NOTICES.md:245`, `:248`, `docs/KNOWN_LIMITATIONS.md:16` | not covered: Owner decisions | see D |

`docs/databases/databricks.md:27` and `docs/databases/snowflake.md:27` were open in the triage
baseline and are resolved on the branch (their receipts are published), so this runbook does not
repeat them.

## D. Not covered

* **Kerberos** (`fabric-auth.md:41`) and **Avro / Schema Registry** (`EMIT.md:281`): no setup, as
  agreed.
* **Fabric Git sync** (`FABRIC_PLATFORM.md:127`): needs a GitHub repository, the workspace
  connected to it in Fabric (portal), and `FABRIC_GIT_REMOTE` / `FABRIC_GIT_TOKEN`. The command is
  `pytest -m live plugins/shape-fabric/tests/test_live_git_sync.py` from a repository checkout with
  the Fabric extras, which is not what the VM holds.
* **Synapse** (`fabric-writers.md:110`): needs a dedicated SQL pool (about 1.20 USD an hour).
* **DuckDB Delta extension** (`TROUBLESHOOTING.md:32`) and **VS Code** (`VSCODE.md:25`): need
  repository files the VM lacks. To cover them, copy the checkout's `tests/fixtures/delta_fallback`
  folder (or `editors/vscode/shape.vsix`) into the VM with `Copy-VMFile` as in A7; `duckdb` is not in
  the VM's extras, and VS Code is not installed there.
* **Hosted GitHub receipts, performance numbers, Owner decisions:** not VM work.

## E. Copy the receipts out and shut down

For each receipt (one at a time):

```powershell
notepad "$rcpt\A1-seed.txt"
```

In Notepad press **Ctrl+A**, then **Ctrl+C**, and paste into the chat with the section id. (Do not
paste anything into the VM in between.) To list what exists: `Get-ChildItem $rcpt | Select-Object Name, Length`.

When all receipts are sent:

**VM — shut down:**

```powershell
Stop-Computer -Force
```

**HOST — checkpoint (optional) and cleanup:**

```powershell
Get-VM ShapeTest | Select-Object Name, State
# only if you want to keep this state: 
Checkpoint-VM -Name ShapeTest -SnapshotName "+ owner-notes receipts 2026-10"
docker exec shape-mysql rm -f /var/lib/mysql-files/orders.csv /var/lib/mysql-files/orders-data.csv
Remove-Item C:\shape-out\* -Force
```

Expected: `State Off`. Fabric items created by section B (`demo_*` tables, `shape_test` schema,
`Files/shape_staging`, the notebook and environment from B7) are test data in `shape-test`; delete
what you do not want kept. Pause or let the trial capacity expire when the workspace is done.

---

## Appendix: flags checked against the 0.9.1 CLI source

Every long option and subcommand used above exists in the CLI of this checkout (`version 0.9.1`).
The table is generated by running each command's `--help` and locating its `add_argument` line;
`file:line` is the argparse definition.

| Command | Option | Defined at (argparse `add_argument`) |
|---|---|---|
| `shape (root)` | `--version` | `src/shape/cli/main.py:1460` |
| `shape deploy-notebook` | `--workspace` | `plugins/shape-fabric/src/shape_fabric/commands.py:478` |
|  | `--auth` | `src/shape/cli/auth.py:41` |
| `shape doctor` | `--delta-table` | `src/shape/cli/doctor.py:125` |
|  | `--fabric` | `src/shape/cli/doctor.py:111` |
|  | `--auth` | `src/shape/cli/auth.py:41` |
|  | `--broker` | `src/shape/cli/doctor.py:117` |
| `shape emit` | `--scale` | `src/shape/cli/emit.py:72` |
|  | `--table` | `src/shape/cli/emit.py:76` |
|  | `--max-events` | `src/shape/cli/emit.py:156` |
|  | `--sink` | `src/shape/cli/emit.py:83` |
|  | `--fresh` | `src/shape/cli/emit.py:277` |
|  | `--json` | `src/shape/cli/emit.py:295` |
|  | `--realtime` | `src/shape/cli/emit.py:103` |
|  | `--rate` | `src/shape/cli/emit.py:109` |
|  | `--yes` | `src/shape/cli/to.py:91` |
|  | `--drift-plan` | `src/shape/cli/emit.py:163` |
|  | `--rows` | `src/shape/cli/emit.py:169` |
|  | `--day-seconds` | `src/shape/cli/emit.py:177` |
|  | `--answer-key` | `src/shape/cli/emit.py:238` |
| `shape from-ddl` | `-o` | `src/shape/cli/main.py:1857` |
| `shape generate` | `--scale` | `src/shape/cli/generation.py:56` |
|  | `--seed` | `src/shape/cli/generation.py:57` |
|  | `--format` | `src/shape/cli/generation.py:65` |
|  | `-o` | `src/shape/cli/generation.py:74` |
|  | `--to` | `src/shape/cli/to.py:37` |
|  | `--write-mode` | `src/shape/cli/to.py:63` |
|  | `--yes` | `src/shape/cli/to.py:91` |
|  | `--sink-config` | `src/shape/cli/scale.py:40` |
|  | `--table-format` | `src/shape/cli/landing.py:29` |
|  | `--batch-date` | `src/shape/cli/landing.py:24` |
|  | `--table-prefix` | `src/shape/cli/scale.py:66` |
|  | `--auth` | `src/shape/cli/auth.py:41` |
|  | `--scale-mode` | `src/shape/cli/scale.py:26` |
|  | `--fabric-workspace` | `src/shape/cli/scale.py:59` |
|  | `--fabric-lakehouse` | `src/shape/cli/scale.py:60` |
| `shape notebook` | `--target` | `plugins/shape-fabric/src/shape_fabric/commands.py:445` |
|  | `-o` | `plugins/shape-fabric/src/shape_fabric/commands.py:442` |
| `shape profile` | `-o` | `src/shape/cli/main.py:1586` |
|  | `--name` | `src/shape/cli/main.py:1631` |
|  | `--auth` | `src/shape/cli/auth.py:41` |
| `shape profile-db` | `--server` | `plugins/shape-sqlserver/src/shape_sqlserver/command.py:36` |
|  | `--database` | `plugins/shape-sqlserver/src/shape_sqlserver/command.py:37` |
|  | `--schema` | `plugins/shape-sqlserver/src/shape_sqlserver/command.py:51` |
|  | `--sample-rows` | `plugins/shape-sqlserver/src/shape_sqlserver/command.py:55` |
|  | `-o` | `plugins/shape-sqlserver/src/shape_sqlserver/command.py:62` |
|  | `--json` | `plugins/shape-sqlserver/src/shape_sqlserver/command.py:63` |
|  | `--auth` | `plugins/shape-sqlserver/src/shape_sqlserver/command.py:38` |
| `shape publish` | `-t` | `plugins/shape-fabric/src/shape_fabric/commands.py:595` |
|  | `--base-path` | `plugins/shape-fabric/src/shape_fabric/commands.py:606` |
|  | `--auth` | `src/shape/cli/auth.py:41` |
|  | `--connection-string` | `src/shape/cli/auth.py:67` |
|  | `--staging-path` | `plugins/shape-fabric/src/shape_fabric/commands.py:612` |
|  | `--database` | `plugins/shape-fabric/src/shape_fabric/commands.py:617` |
| `shape seed` | `--target` | `src/shape/cli/seed.py:31` |
|  | `--scale` | `src/shape/cli/seed.py:32` |
|  | `--seed` | `src/shape/cli/seed.py:33` |
|  | `--mode` | `src/shape/cli/seed.py:34` |
|  | `--dry-run` | `src/shape/cli/seed.py:40` |
| `shape setup-fabric` | `--workspace` | `plugins/shape-fabric/src/shape_fabric/commands.py:518` |
|  | `--create-lakehouse` | `plugins/shape-fabric/src/shape_fabric/commands.py:519` |
|  | `--lakehouse-name` | `plugins/shape-fabric/src/shape_fabric/commands.py:527` |
|  | `--snippet` | `plugins/shape-fabric/src/shape_fabric/commands.py:532` |
| `shape stream` | `-t` | `src/shape/cli/emit.py:76` |
|  | `--to` | `src/shape/cli/to.py:37` |
|  | `--commit-rows` | `src/shape/cli/to.py:57` |
|  | `--realtime` | `src/shape/cli/emit.py:103` |
|  | `--rate` | `src/shape/cli/emit.py:109` |
|  | `--yes` | `src/shape/cli/to.py:91` |
|  | `--roll-seconds` | `src/shape/cli/to.py:54` |
|  | `--checkpoint-seconds` | `src/shape/cli/emit.py:258` |
|  | `--sink-config` | `src/shape/cli/to.py:78` |
| `shape stream-profile` | `-o` | `src/shape/cli/main.py:1343` |
|  | `--window` | `src/shape/cli/main.py:1344` |
|  | `--size` | `src/shape/cli/main.py:1351` |
|  | `--allowed-lateness` | `src/shape/cli/main.py:1354` |
|  | `--windows` | `src/shape/cli/main.py:1350` |
|  | `--checkpoint` | `src/shape/cli/main.py:1420` |
|  | `--follow` | `src/shape/cli/main.py:1400` |
|  | `--idle-timeout` | `src/shape/cli/main.py:1407` |

Commands used with positional arguments only (checked to exist in `shape --help`): `shape jobs list`,
`shape jobs status JOB`, `shape jobs cancel JOB`, `shape jobs resume JOB` (`src/shape/cli/jobs.py`),
`shape inspect FILE`. The `Shp` helper, `Fresh` and `Stamp` are PowerShell, not Shape.

## Appendix: environment variables this runbook reads

| Variable | Where it is set | Read by |
|---|---|---|
| `SHAPE_POSTGRES_PASSWORD`, `SHAPE_MYSQL_PASSWORD`, `SHAPE_MSSQL_PASSWORD` | session block (2.3) | the PostgreSQL / MySQL sinks; the SQL Server `env://` option |
| `SHAPE_CONFIRM_REMOTE` | session block | every write to a non-local target |
| `SHAPE_KAFKA_SERVERS` | already a user variable | A2, A5, A9 |
| `EVENTHUBS_NAMESPACE_NAME`, `SHAPE_EVENTHUBS_CONNECTION_STRING` | already user variables | A3, A5 |
| `AZURE_STORAGE_ACCOUNT_NAME`, `AZURE_STORAGE_ACCOUNT_KEY` | already user variables | B1, B2 (`env://AZURE_STORAGE_ACCOUNT_KEY`) |
| `FABRIC_SQL_ENDPOINT`, `FABRIC_KQL_HOST`, `FABRIC_KQL_DB` | already user variables | B4, B5, B6 |
| `SHAPE_SQL_CONNECTION`, `WH` | typed in B6 | `--connection-string env://…` |
| `SHAPE_FABRIC_TOKEN` | B9 | `--scale-mode fabric_spark`, `shape jobs` |
