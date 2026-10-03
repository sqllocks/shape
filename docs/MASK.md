# Masking personal data: `shape mask`

`shape mask` replaces personal data in data files with synthetic values of the same format, so
the files can be shared or used for testing. The same code is the `mask` built-in of the
`shape.transforms` group, usable from Python.

```bash
shape mask ./real_data/ -o ./masked/                  # every CSV file in the directory
shape mask customers.csv -o ./masked/ --seed 7
shape mask ./real_data/ -o ./masked/ --format parquet
shape mask orders.csv -o ./masked/ --exclude notes --pii token=ssn --json
```

| Option | Meaning |
|---|---|
| `PATH` | a CSV or Parquet file, or a directory with one file per table |
| `-o DIR` | where the masked files go (one per table, same file name and format); the command refuses a directory that would overwrite its input |
| `--format csv\|parquet` | the format of a directory's files, and of the output (default: the file's extension; `csv` for a directory) |
| `--seed N` | the output is the same for the same input and seed (default 42); unused with a key |
| `--key-file FILE` / `--key-env VAR` | keyed masking (below): read the secret key from a file or an environment variable |
| `--exclude COLUMN` | leave this column as it is (repeatable) |
| `--pii COLUMN=TYPE` | mask this column as `TYPE` even if nothing marks it as personal data (repeatable) |
| `--json` | print what was masked as JSON |

Exit codes: `0` done, `2` the input could not be read or the request is not possible.

## What is masked

A column is masked when its **name** points to a type (`email`, `e_mail`, `Phone_Number`,
`shipZipCode`, `ssn`, `ip_address`, `dob`, ...; the longest matching phrase decides, so
`ip_address` is an IP address and `street_address` a street address) or, where the name says
nothing, when Shape's profile engine finds a value pattern in it (e-mail, phone, SSN, IPv4, IBAN,
postal code; a UUID is not personal data). Names are matched by whole words, so `description` or
`business` are not masked. A name match is ignored when the column cannot hold the type: a
timestamp column called `last_login` is left alone, a numeric column is masked only as a number
type (`zip`, `phone`, `ssn`, `credit_card`).

Types: `email`, `phone`, `first_name`, `last_name`, `name`, `address`, `city`, `state`, `zip`,
`ssn`, `credit_card`, `ip_address`, `username`, `date_of_birth`, `iban`.

## What a masked column keeps

- **Its type and its nulls.** An integer column stays an integer column, with the same number
  of digits; a null stays a null. Unmasked columns are written exactly as they were read (CSV
  values are never re-typed).
- **The format of the values.** A phone number keeps its punctuation and its country code, a
  ZIP+4 stays a ZIP+4, a card number stays Luhn-valid, an IBAN keeps its country and a valid check
  digit, an IPv4 address stays one, a name keeps its upper or lower case, a date keeps its format
  and lies in the range of the original dates.
- **Its joins.** The same original value gets the same replacement wherever it appears (in a
  key, in the columns of other tables that hold it, and in a column that is a detected foreign key
  to a masked key, whatever its name), so joins and group-bys still work. A value with no parent
  is masked too; it never stays as it was.

## What it guarantees

- No masked value equals the original value of its cell.
- For identifier types (everything except first and last names, cities, states, ZIP codes and
  dates of birth) no replacement is any original value of that type, and replacements are
  distinct from each other, so a unique column stays unique and no original value is reused.
- If the replacements cannot be found (a domain too small), the command fails; it never writes an
  original value.

It does not look inside free text: an e-mail address inside a `notes` column is not found. Use
`--exclude` and `--pii` to adjust the choice of columns, and read the summary it prints.

## From Python

```python
from shape.plugins.host import default_host

mask = default_host().get("shape.transforms", "mask")
masked_tables = mask.apply({"customers": table}, seed=7)    # dict of Arrow tables
result = mask.mask({"customers": table}, seed=7)            # also: result.columns_masked
```

## Keyed masking

With a key, a value's mask depends only on the key, the kind of value and the value itself: not
on the other rows, the table, the column, the order of rows or the run. Mask a table today and a
new extract next month with the same key and the same customer gets the same mask, so joins hold
across extracts. Without a key (`--seed`), masks depend on the whole set of values in the run.

```bash
export SHAPE_MASK_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
shape mask ./real_data/ -o ./masked/ --key-env SHAPE_MASK_KEY
shape mask ./real_data/ -o ./masked/ --key-file ~/.config/mykey      # chmod 600
```

Keyed mode needs the `cryptography` package (`pip install 'sqllocks-shape[sign]'`). It masks
e-mail, phone, first name, last name, name, SSN, ZIP and date-of-birth columns value by value.
Other detected types (address, city, state, IP address, user name, card number, IBAN) and numeric
columns use a random generator seeded from the key: the same key and the same input data give the
same output, but a value's mask can change when the other values in the data change. If two
different e-mails, phone numbers or SSNs would get the same mask, or a mask would equal an
original, the command fails rather than merge them.

### Key handling

- The key is at least 16 bytes. Make one with `python -c 'import secrets; print(secrets.token_hex(32))'`
  or `shape.masking.generate_key()`.
- Keep it in a secret store, an environment variable or a file only you can read (`chmod 600`).
  A key file readable by group or others is refused (POSIX).
- Shape never writes the key: not into the masked files, not into the summary, and the command
  refuses a key file that lies inside the output directory. Keep the key apart from the masked
  data; anyone who has both can test guesses against the data, and whoever holds the key can mask
  guessed values and compare.
- There is no way to compute the original from a mask, but whoever holds the key can mask guessed
  values and compare. Losing the key means a later extract can no longer be masked to match the
  earlier one.
- Rotating the key changes every mask. Plan a rotation as a full re-mask.

## Public masking API: `shape.masking`

```python
from shape.masking import Masker, load_key, generate_key

masker = Masker(load_key(env="SHAPE_MASK_KEY"))
masker.mask("identifier", "AB-1234-xy")          # 'HQ-6368-yw' style: layout and case kept
masker.mask("name", "Alice Johnson")              # part="first" | "last" | "full"
masker.mask("email", "ada@corp.io")               # always ...@example.com / .org / .net
masker.mask("phone", "+44 20 7946 0958")          # country code and punctuation kept
masker.mask("date", "2021-03-04", max_days=30)    # shifted by 1..30 days either way
masker.mask("date", d, subject="patient-7")       # one shift per subject: intervals are kept
masker.mask("text", "mail ada@corp.io, 10.1.2.3") # e-mails, phones, SSNs, IPv4 inside text
masked = masker.mask_tables(tables, {"orders": {"customer_id": "identifier"}})
column = masker.mask_column("identifier", arrow_array)
```

| Kind | Input | Output |
|---|---|---|
| `identifier` | text or integer | digits stay digits, letters stay letters of the same case, other characters stay; an integer stays an integer of the same width |
| `name` | text | a first and/or last name from fixed lists, in the case style of the input (`Last, First` is kept) |
| `email` | text | `<first><sep><last><3 digits>@example.com|org|net`; the input is trimmed and lower-cased first |
| `phone` | text | digits replaced, punctuation and a leading `+<country code>` kept |
| `date` | `date`, `datetime` or text (ISO or a common layout) | the same type and layout, shifted by a non-zero number of days within `max_days` (default 365) |
| `text` | text | e-mail, phone, SSN and IPv4 patterns inside it are replaced exactly as the matching kinds would; nothing else (names in prose are not found) |

A masked value never equals its input; if no replacement can differ (`"---"`), `MaskingError` is
raised. `None` stays `None`. Integer, text, date and timestamp columns are supported by
`mask_column`.

**Referential consistency.** A mask is a pure function of (key, kind, value). The same value in
any table or column masked as the same kind gets the same mask, in any run on any machine, in
any batch size. `mask_tables` raises `MaskingError` if two different identifying values
(identifier, email, phone) would get the same mask.

**Stability promise.** `MASKING_API_VERSION` is `"1.0"`. Within 1.x the names in
`shape.masking.__all__`, the kinds in `KINDS`, the signatures above and the output of every kind
for every input and key stay the same. The name lists the masks draw from are frozen with them.
A change that would alter outputs needs a new major version of the masking API. Tests:
`tests/masking/test_masking_api.py` (conformance) and `tests/masking/test_masking_compat.py`
(golden outputs, frozen lists, signatures).

**Security notes.** Masks come from HMAC-SHA256 (the `cryptography` package) over the key, the
kind, and the value. A small input domain (a 4-digit code, a birth year) can be searched
completely by anyone who has the key, so format-preserving masking is pseudonymization, not
anonymization.

## Changes to existing behaviour

- Masked e-mail addresses now use only the reserved domains `example.com`, `example.org` and
  `example.net` (before, they used the domains of real mail providers). Everything else in
  unkeyed output is unchanged for a given input and seed.
