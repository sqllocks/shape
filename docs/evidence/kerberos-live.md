# Kerberos keytab sign-in to a Windows-authentication-only SQL Server — Owner-run receipt

Owner-run on **2026-10-12** (15:15–15:25 Eastern) on the Owner's Hyper-V host, with the lead
session giving each step. Resolves the owner note on [`--auth kerberos`](../plugins/fabric-auth.md)
example 0. All output below is copied from the Owner's terminals; nothing was edited except
wrapping. No password, keytab bytes or ticket contents are recorded.

## Environment

| item | value |
|---|---|
| Domain | `CORP.EXAMPLE` (NetBIOS `CORP`), a disposable lab forest |
| `DC01` | Windows Server 2022 Standard Evaluation; AD DS domain controller (KDC) **and** SQL Server 2022 Developer (RTM) 16.0.1000.6, default instance, service account `CORP\svc_sql` |
| SQL Server authentication | Windows authentication only (`SERVERPROPERTY('IsIntegratedSecurityOnly') = 1`) |
| SPNs on `CORP\svc_sql` | `MSSQLSvc/dc01.corp.example:1433`, `MSSQLSvc/dc01.corp.example` |
| `LNX01` | Ubuntu 24.04.5 LTS; Shape **0.9.1** from PyPI (`sqllocks-shape`, `sqllocks-shape-domains`, `sqllocks-shape-fabric[sqlserver]`, `sqllocks-shape-sqlserver`); `krb5-user` 1.20.1-6ubuntu2.10; `msodbcsql18` 18.7.1.1-1; `unixodbc` 2.3.12-1ubuntu0.24.04.1 |
| Keytab | `svc_shape@CORP.EXAMPLE`, AES-256 (`ktpass /crypto AES256-SHA1`), KVNO 3, at `/etc/shape/svc.keytab` mode 600 |
| Network | one Hyper-V internal switch, 10.77.0.0/24 (DC01 .10, LNX01 .12); LNX01 resolved `dc01.corp.example` from `/etc/hosts` |

The domain controller's AD-integrated DNS zone was not created during this lab's promotion, so
LNX01 named the KDC directly in `/etc/krb5.conf` (`dns_lookup_kdc = false`) and resolved the host
from `/etc/hosts`. This does not affect what is tested: the tickets, accounts and SPNs are Active
Directory's.

## 1. Versions (LNX01)

```text
PS> shape --version
shape 0.9.1
exit=0
PS> dpkg-query -W krb5-user msodbcsql18 unixodbc
krb5-user       1.20.1-6ubuntu2.10
msodbcsql18     18.7.1.1-1
unixodbc        2.3.12-1ubuntu0.24.04.1
exit=0
PS> odbcinst -q -d
[ODBC Driver 18 for SQL Server]
exit=0
PS> lsb_release -d
Description:    Ubuntu 24.04.5 LTS
exit=0
```

Server (DC01):

```text
Microsoft SQL Server 2022 (RTM) - 16.0.1000.6 (X64)
        Oct  8 2022 05:58:25
        Copyright (C) 2022 Microsoft Corporation
        Developer Edition (64-bit) on Windows Server 2022 Standard Evaluation 10.0 <X64> (Build 20348: ) (Hypervisor)

IsIntegratedSecurityOnly = 1
Microsoft Windows Server 2022 Standard Evaluation
```

## 2. Kerberos without Shape (LNX01)

```text
PS> getent hosts dc01.corp.example
10.77.0.10      dc01.corp.example dc01
exit=0
PS> kinit -k -t /etc/shape/svc.keytab svc_shape@CORP.EXAMPLE
exit=0
PS> klist
Ticket cache: FILE:/tmp/krb5cc_1000
Default principal: svc_shape@CORP.EXAMPLE

Valid starting       Expires              Service principal
10/12/2026 19:23:19  10/13/2026 05:23:19  krbtgt/CORP.EXAMPLE@CORP.EXAMPLE
        renew until 10/13/2026 05:23:19
exit=0
PS> kvno MSSQLSvc/dc01.corp.example:1433
MSSQLSvc/dc01.corp.example:1433@CORP.EXAMPLE: kvno = 2
exit=0
PS> kdestroy
exit=0
```

## 3. The test (LNX01)

The first attempt left out `--yes` and was refused by Shape's remote-write confirmation, as
documented:

```text
PS> shape generate retail --to 'mssql://dc01.corp.example/shop?trust_server_certificate=true' --auth kerberos --keytab file:///etc/shape/svc.keytab --principal svc_shape@CORP.EXAMPLE
shape: error: refusing to write to non-local target mssql://dc01.corp.example/shop?trust_server_certificate=true without confirmation; pass --yes or set SHAPE_CONFIRM_REMOTE=1
exit=2
```

With `--yes`:

```text
PS> shape generate retail --to 'mssql://dc01.corp.example/shop?trust_server_certificate=true' --auth kerberos --keytab file:///etc/shape/svc.keytab --principal svc_shape@CORP.EXAMPLE --yes
Wrote 21,750 rows in 9 tables to mssql://dc01.corp.example/shop?trust_server_certificate=true (2.42s)
exit=0
```

`trust_server_certificate=true` is needed because this lab SQL Server uses its self-signed
certificate and ODBC Driver 18 encrypts by default.

## 4. What the server saw (DC01)

A watcher polling `sys.dm_exec_connections` joined to `sys.dm_exec_sessions` every 0.5 s for
logins named `CORP\svc_shape` recorded, during the run above:

```text
3:21:14 PM watching for CORP\svc_shape connections for 3 minutes...
3:22:08 PM  session=51  login=CORP\svc_shape  auth_scheme=KERBEROS  from=10.77.0.12  encrypt=TRUE
3:22:08 PM  session=52  login=CORP\svc_shape  auth_scheme=KERBEROS  from=10.77.0.12  encrypt=TRUE
```

Afterwards, in `shop`:

```text
customers = 1000
tables in shop = 9
```

## 5. Negative controls (LNX01)

A keytab other users can read is refused before `kinit` runs:

```text
PS> shape generate retail --to mssql://dc01.corp.example/shop?trust_server_certificate=true --auth kerberos --keytab file:///etc/shape/svc.keytab --principal svc_shape@CORP.EXAMPLE --yes
shape: error: --keytab file:///etc/shape/svc.keytab: credential file /etc/shape/svc.keytab is accessible to other users (mode 0644): run chmod 600 /etc/shape/svc.keytab and try again
exit=2
```

A principal the keytab has no key for:

```text
PS> shape generate retail --to mssql://dc01.corp.example/shop?trust_server_certificate=true --auth kerberos --keytab file:///etc/shape/svc.keytab --principal nobody@CORP.EXAMPLE --yes
shape: error: kinit failed for nobody@CORP.EXAMPLE with keytab file:///etc/shape/svc.keytab: kinit: Keytab contains no suitable keys for nobody@CORP.EXAMPLE while getting initial credentials
exit=2
```

The keytab was returned to mode 600 (`-rw------- 1 shape shape 78 Oct 12 19:19 /etc/shape/svc.keytab`).

## Related

[`--auth kerberos`](../plugins/fabric-auth.md) · [Published receipts](index.md)
