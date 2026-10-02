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
| `--seed N` | the output is the same for the same input and seed (default 42) |
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
