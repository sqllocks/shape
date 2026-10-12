# Kerberos live test: `--auth kerberos` against a Windows-authentication-only SQL Server

Resolves the owner note at `docs/plugins/fabric-auth.md` (example 0, `--auth kerberos`).
Nothing here was run: it is the exact setup the Owner needs. The note is a test of Active
Directory and SQL Server Windows authentication from Linux. It does **not** need a Fabric
account (the page's "Needs a Fabric account" line is wrong for this example and is corrected
when the receipt lands).

Why this cannot run in CI: SQL Server's Kerberos path needs an Active Directory identity
(`MSSQLSvc` SPN, AD-backed logins). A standalone MIT KDC does not supply those, and a Linux
runner cannot host a Windows Server domain controller. Hence the Owner's Hyper-V host.

Rules: use disposable names and passwords; never commit a keytab, password or `krb5.conf`
with real secrets. The receipt below contains none.

## 0. What you need

| item | value used below (change freely, keep it consistent) |
|---|---|
| Hyper-V host | Windows 11 Pro / Server, 16 GB RAM free, 100 GB disk |
| VM `DC01` | Windows Server 2022 evaluation, 2 vCPU, 4 GB, AD DS + DNS |
| VM `SQL01` | Windows Server 2022 + SQL Server 2022 Developer, 2 vCPU, 6 GB, domain-joined |
| VM `LNX01` | Ubuntu 24.04, 2 vCPU, 4 GB, runs Shape |
| domain | `CORP.EXAMPLE` (NetBIOS `CORP`) |
| switch | one Hyper-V *Internal* switch `shape-kdc`, 10.77.0.0/24: DC01 10.77.0.10, SQL01 10.77.0.11, LNX01 10.77.0.12 |

All three VMs share one switch and one clock (Kerberos tolerates 5 minutes of skew; leave
Hyper-V time synchronisation on, and check in step 7).

## 1. Hyper-V switch (host, elevated PowerShell)

```powershell
New-VMSwitch -Name shape-kdc -SwitchType Internal
Get-NetAdapter | Where-Object Name -like '*shape-kdc*' |
  New-NetIPAddress -IPAddress 10.77.0.1 -PrefixLength 24
```

Create the three VMs on `shape-kdc` (Generation 2; for LNX01 disable Secure Boot or pick the
"Microsoft UEFI Certificate Authority" template).

## 2. Domain controller `DC01`

In an elevated PowerShell on DC01 (static IP 10.77.0.10/24, DNS 127.0.0.1):

```powershell
Rename-Computer DC01 -Restart
# after reboot:
Install-WindowsFeature AD-Domain-Services,DNS -IncludeManagementTools
Install-ADDSForest -DomainName CORP.EXAMPLE -DomainNetbiosName CORP `
  -SafeModeAdministratorPassword (Read-Host -AsSecureString 'DSRM password') -Force
```

After the reboot, create the accounts (all passwords throwaway):

```powershell
$pw = Read-Host -AsSecureString 'password for both accounts'
New-ADUser -Name svc_sql   -SamAccountName svc_sql   -AccountPassword $pw -Enabled $true -PasswordNeverExpires $true
New-ADUser -Name svc_shape -SamAccountName svc_shape -AccountPassword $pw -Enabled $true -PasswordNeverExpires $true
# AES keys for both (Linux MIT Kerberos prefers AES):
Set-ADUser svc_sql   -KerberosEncryptionType AES128,AES256
Set-ADUser svc_shape -KerberosEncryptionType AES128,AES256
```

## 3. SQL Server `SQL01`

1. Static IP 10.77.0.11/24, DNS 10.77.0.10; `Add-Computer -DomainName CORP.EXAMPLE -Credential CORP\Administrator -Restart`.
2. Install SQL Server 2022 Developer, database engine only. **Authentication mode: Windows
   authentication mode** (not mixed). **Service account: `CORP\svc_sql`.**
3. Register the SPN on the service account (on DC01 or any domain admin shell):

   ```powershell
   setspn -S MSSQLSvc/sql01.corp.example:1433 CORP\svc_sql
   setspn -S MSSQLSvc/sql01.corp.example      CORP\svc_sql
   setspn -L CORP\svc_sql
   ```
4. Enable TCP/IP on port 1433 (SQL Server Configuration Manager), restart the service, open
   TCP 1433 in Windows Firewall.
5. In SSMS, as a sysadmin:

   ```sql
   CREATE DATABASE shop;
   GO
   CREATE LOGIN [CORP\svc_shape] FROM WINDOWS;
   GO
   USE shop;
   CREATE USER [CORP\svc_shape] FOR LOGIN [CORP\svc_shape];
   ALTER ROLE db_owner ADD MEMBER [CORP\svc_shape];
   ```

## 4. Client keytab for `svc_shape` (on DC01)

```powershell
ktpass /princ svc_shape@CORP.EXAMPLE /mapuser CORP\svc_shape /pass * `
  /ptype KRB5_NT_PRINCIPAL /crypto AES256-SHA1 /out C:\svc_shape.keytab
```

(`/pass *` prompts for the account password from step 2.) Copy `svc_shape.keytab` to LNX01
(`scp`, or a Hyper-V file copy: `Copy-VMFile`). Delete it from DC01 afterwards.

## 5. Linux client `LNX01`

```bash
sudo apt-get update
sudo apt-get install -y krb5-user unixodbc-dev curl gpg ntpsec-ntpdate python3-venv
# Microsoft ODBC Driver 18
curl -sSL https://packages.microsoft.com/keys/microsoft.asc | sudo gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/ubuntu/24.04/prod noble main" | sudo tee /etc/apt/sources.list.d/mssql-release.list
sudo apt-get update && sudo ACCEPT_EULA=Y apt-get install -y msodbcsql18
```

Static IP 10.77.0.12/24 with **DNS 10.77.0.10** (so `sql01.corp.example` resolves and the KDC is
found), then `/etc/krb5.conf`:

```ini
[libdefaults]
  default_realm = CORP.EXAMPLE
  dns_lookup_kdc = true
  rdns = false
  default_tkt_enctypes = aes256-cts-hmac-sha1-96 aes128-cts-hmac-sha1-96
[realms]
  CORP.EXAMPLE = { kdc = dc01.corp.example  admin_server = dc01.corp.example }
[domain_realm]
  .corp.example = CORP.EXAMPLE
  corp.example = CORP.EXAMPLE
```

Keytab and Shape:

```bash
sudo install -d -m 700 /etc/shape
sudo install -m 600 ~/svc_shape.keytab /etc/shape/svc.keytab     # Shape refuses anything but mode 600
python3 -m venv ~/shape-env && . ~/shape-env/bin/activate
pip install 'sqllocks-shape[azure]' sqllocks-shape-domains sqllocks-shape-mssql pyodbc
```

(Use the 0.9.1 wheels or the checkout, whichever the Owner is testing. `shape --version` must
print 0.9.1 or newer.)

## 6. Preconditions checked before Shape runs (all must pass)

```bash
getent hosts sql01.corp.example                    # 10.77.0.11
sudo ntpdate -q 10.77.0.10                         # offset well under 5 minutes
kinit -k -t /etc/shape/svc.keytab svc_shape@CORP.EXAMPLE && klist     # a krbtgt ticket
kvno MSSQLSvc/sql01.corp.example:1433              # a service ticket, kvno = N
kdestroy
```

If `kvno` fails with "Server not found in Kerberos database", the SPN (step 3.3) is missing or
registered on the wrong account; fix that before continuing. These four lines prove the AD
setup without Shape and are part of the receipt.

## 7. The test

Exactly the page's command (host name is already `sql01.corp.example`), after confirming the
database is empty:

```bash
shape generate retail --to mssql://sql01.corp.example/shop \
  --auth kerberos --keytab file:///etc/shape/svc.keytab --principal svc_shape@CORP.EXAMPLE \
  2>&1 | tee ~/kerberos-generate.txt
echo "exit=$?" | tee -a ~/kerberos-generate.txt
```

Then prove the server saw Kerberos, not a password. In SSMS on SQL01:

```sql
SELECT s.session_id, s.login_name, c.auth_scheme, c.client_net_address
FROM sys.dm_exec_connections c JOIN sys.dm_exec_sessions s ON s.session_id = c.session_id
WHERE s.login_name = 'CORP\svc_shape';
SELECT COUNT(*) AS customers FROM shop.dbo.customer;
```

Run the first query *while* Shape is loading (rerun the generate with a larger `--scale PRESET` (see `shape presets`) if it
finishes too fast) or enable a login audit; `auth_scheme` must be `KERBEROS`.

Negative control, for the same receipt: with the keytab at mode 644 Shape must refuse before
`kinit` (`chmod 644 /etc/shape/svc.keytab`, rerun, expect exit 2, then `chmod 600`). With a
wrong principal (`--principal nobody@CORP.EXAMPLE`) expect exit 2 and `kinit failed for ...`.

## 8. What receipt resolves the note

Put one file in `docs/plans/evidence/owner-notes/` named
`kerberos-<date>-<shape-version>.md` containing, in this order, nothing but real output:

1. `shape --version`, `krb5-config --version`, ODBC `odbcinst -q -d`, Ubuntu release, Windows
   Server and SQL Server `SELECT @@VERSION` lines (host names allowed; no passwords).
2. The step 6 command output (klist expiry times may stay).
3. `~/kerberos-generate.txt` in full, including `exit=0`.
4. The `sys.dm_exec_connections` row with `auth_scheme = KERBEROS` and the `customers` count.
5. The two negative-control outputs.

Then, with that file committed, change `docs/plugins/fabric-auth.md` example 0: replace the
"Needs a Fabric account" line with "Needs an Active Directory domain and SQL Server (Windows
authentication). Not run in CI.", paste item 3 as the example's `.expected` output, replace
the `<!-- owner: ... -->` line with an `Evidence:` link to the receipt file (public pages must
use an absolute `https://github.com/...` Evidence link as the other owner examples do, never a
relative link into `docs/plans/`), and run `python scripts/check_docs_owners.py`: the count drops
by one.

A failure of any step 7 command is a result too: save the full output as the receipt and
report it; do not edit the expected output to match.

## 9. Tear-down

`Stop-VM DC01,SQL01 ; Remove-VM ... ; Remove-VMSwitch shape-kdc`, delete the VHDX files, and
shred `/etc/shape/svc.keytab` on LNX01. The keytab is the `svc_shape` password in file form.
