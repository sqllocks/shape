# Shape 0.9.1 release tag and digest check

Status: available.

Needs a GitHub account. Not run in CI. Commands and output are historical evidence, not commands executed by the docs harness.

These receipts back the two worked examples in the [release checklist](../RELEASE_CHECKLIST.md) (sections 5 and 6). They use the published Shape 0.9.1 release. Reproduced on 2026-10-12 at 3:36 PM Eastern from a clone of the repository and the public PyPI file; nothing was edited except wrapping.

## The tag

- Tag object: `55c34315d24dd5f06fecd8902fa22391118a3bf9`, an annotated tag `v0.9.1`.
- Commit it points to: `d09731e59b630fac11b7127c812c47ccca8ab64f`.
- Tagger date: 2026-10-11 at 3:30:28 PM Eastern (`1791747028 -0400`, 19:30:28 UTC); tag message `Shape 0.9.1`.

```text
$ git ls-remote --tags origin v0.9.1 'v0.9.1^{}'
55c34315d24dd5f06fecd8902fa22391118a3bf9	refs/tags/v0.9.1
d09731e59b630fac11b7127c812c47ccca8ab64f	refs/tags/v0.9.1^{}
```

```text
$ git cat-file -p v0.9.1
object d09731e59b630fac11b7127c812c47ccca8ab64f
type commit
tag v0.9.1
tagger Jonathan Stewart <29076762+sqllocks@users.noreply.github.com> 1791747028 -0400

Shape 0.9.1
```

## The tag-triggered Publish run

- Run: [38168312038](https://github.com/sqllocks/shape/actions/runs/38168312038); workflow `Publish`, event `push`, ref `v0.9.1`, head SHA `d09731e59b630fac11b7127c812c47ccca8ab64f`.
- Created 2026-10-11 at 3:30:30 PM Eastern; last updated 4:54:33 PM Eastern; attempt 4; conclusion `success`.
- It called `release.yml` and `wheels.yml` at the same commit, and its `release-sbom` artifact (ID 11713208234, digest `sha256:d26f4eec075ccc97ebc775e42ccf7b93c38706a94e0dd4d74a4f1c19cdac7f72`) holds `SHA256SUMS`.

## The digest check

The `py3-none-any` wheel was downloaded from PyPI (`sqllocks_shape-0.9.1-py3-none-any.whl`, 2,822,462 bytes) and checked against its line of that run's `SHA256SUMS`:

```text
$ grep ' sqllocks_shape-0.9.1-py3-none-any.whl$' SHA256SUMS | sha256sum -c -
sqllocks_shape-0.9.1-py3-none-any.whl: OK
```

The line is `dc2b1866ee1f7f0de9b1c59dc549eb6295b874dde42f10a012f1c794e85d4a77  sqllocks_shape-0.9.1-py3-none-any.whl`. So the file PyPI serves is the one the tag run built.

## The attestation check

The same run attested every archive. `gh attestation verify` output for this wheel is not recorded here yet; it needs an authenticated GitHub CLI session and is added when the Owner supplies it.

## Related

[Release checklist](../RELEASE_CHECKLIST.md) · [Verify a release](../VERIFY.md)
