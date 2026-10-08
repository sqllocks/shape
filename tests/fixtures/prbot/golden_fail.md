<!-- shape-pr-comment -->
## Shape data check

**Verdict: fail** - 1 source, 2 findings, 1 planned change

### Source: customers

`shape diff` - **fail**

| Table | Column | Kind | Severity |
|---|---|---|---|
| customers | email | null\_rate\_change | high |
| customers | tier | new\_category | medium |

Planned changes (not counted as findings):

| Table | Column | Kind | Plan |
|---|---|---|---|
| customers | signup | range\_change | chg-12 |

---
<sub>Shape 0.0.0</sub>
