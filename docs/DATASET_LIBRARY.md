# The public dataset library

Status: experimental.

[Owner: documentation maintainer — execute the removed command examples in a suitable local or test-account environment and record their complete output before restoring them.]


Realistic synthetic data usually starts from a profile of your own data. The dataset library
removes that first step: it ships **safe profiles of public datasets** (statistics and formats,
never rows), so you can try Shape, write tests or run a demo with no data of your own.

Use [the tested starters](TUTORIAL.md) for local commands and complete output.

`dataset:NAME` is accepted wherever `shape generate --from` takes a `.shape` file. It reads the
profile that ships inside Shape and needs no network. (`library:NAME` is the prefix of
[scenarios](SCENARIO_LIBRARY.md), not of datasets.)

## What is in it

| Name | Rows | Licence | Source |
|---|---|---|---|
| `palmer-penguins` | 344 | `CC0-1.0` | Palmer Station LTER, via the palmerpenguins package |
| `iris` | 150 | `CC-BY-4.0` | UCI Machine Learning Repository |
| `wine` | 178 | `CC-BY-4.0` | UCI Machine Learning Repository |
| `abalone` | 4177 | `CC-BY-4.0` | UCI Machine Learning Repository |
| `adult-income` | 48842 | `CC-BY-4.0` | UCI Machine Learning Repository |
| `breast-cancer-wisconsin` | 569 | `CC-BY-4.0` | UCI Machine Learning Repository |

`shape library show NAME` prints the source URL, the SHA-256 of the file the profile was built
from, the retrieval date and the attribution. Every dataset is credited in
`THIRD_PARTY_NOTICES.md`; if you redistribute data generated from a CC BY 4.0 profile, keep that
credit.

## Which datasets qualify

Only datasets under one of these licences join the library. The list is fixed:

| Licence id | Meaning |
|---|---|
| `CC0-1.0` | Creative Commons CC0 1.0 Universal (public domain dedication) |
| `CC-BY-4.0` | Creative Commons Attribution 4.0 International |
| `public-domain` | a work in the public domain, with the source that says so |

A dataset under any other licence (for example a non-commercial or share-alike licence, or one
with no licence at all) is not added, however useful it is. Raw rows are never shipped, whatever
the licence.

## The index

`src/shape/library/datasets/index.json` is `{"format": "shape-dataset-library", "version": 1,
"datasets": [...]}`. Each entry has:

| Key | Meaning |
|---|---|
| `name` | the slug used in `dataset:NAME` |
| `title` | a readable name |
| `source_url` | the https address of the file the profile was built from |
| `license` | one of the licence ids above |
| `attribution` | the credit the licence asks for |
| `retrieved` | the ISO date the source was downloaded |
| `source_sha256` | the SHA-256 of that file |
| `rows` | the number of rows of the source |
| `profile` | the profile's file name in the same folder |

Shape refuses an index of another format, a newer version, a licence that is not on the list, a
source that is not https, a checksum that is not 64 hex digits and a name listed twice. Each
profile is under 256 KB and the whole library adds under 2 MB to the wheel; tests check both.

## How a profile is built

A profile is a **safe capture** (the default capture of `shape profile`): a column that looks
sensitive keeps statistics and formats only, and a category value is kept only where at least five
rows carry it. `shape profile validate --safe` passes for every shipped profile.

`python scripts/build_dataset_library.py NAME` is the only code in the project that touches the
network. It

1. downloads the source file (https only),
2. checks its SHA-256 against the one in the index (a first build records it),
3. profiles the table with the default safe capture, and
4. writes `NAME.shape` and updates the index entry.

No source data is written anywhere in the repository. If the source changed since it was recorded,
the script stops with exit 1 and names both checksums; look at what changed, then build again with
`--refresh` to accept it (the checksum and the retrieval date are updated). `--from-file FILE`
profiles a copy you already have instead of downloading it, and the checksum is still checked.
Exit 2 means an unknown name or a source that cannot be used.

## Proposing a dataset

Open an issue with the dataset's name, its source URL, the licence (with the page that states it),
the credit it asks for and why it is a good example. If the licence is on the list above, a
maintainer adds it to `SOURCES` in `scripts/build_dataset_library.py`, builds the profile, adds the
credit to `THIRD_PARTY_NOTICES.md` and a row to the table above. Datasets with personal data are
not added unless the licence and the source make clear that the data was released for reuse.
