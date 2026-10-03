# Shape by SQLLocks — naming contract

The product name is **Shape** and the publisher/community identity is **SQLLocks**.

- Product: Shape
- Public branding: Shape by SQLLocks
- Core concept: Shape as Code
- PyPI distribution: `sqllocks-shape`
- Python import: `shape`
- CLI executable: `shape`
- Artifact extension: `.shape`
- GitHub repository: `sqllocks/shape`
- Hosted control plane: Shape Hub
- Web experience: Shape Studio
- Package/domain ecosystem: Shape Packs

The distribution namespace MUST NOT be confused with the Python import namespace. Installation is `pip install sqllocks-shape`; application code continues to use `import shape`.

## What this repository covers

This open-source repository, `sqllocks/shape` (MIT), covers Shape itself: the `sqllocks-shape` distribution, its first-party plugins (`sqllocks-shape-*` built from `plugins/`), the `shape` CLI and the `.shape` format. Shape Hub, Shape Studio and Shape Packs are product names in the Shape family; they are not built from this repository, and this repository does not depend on them. Packs built by anyone through the public plugin entry points are welcome under their own names.
