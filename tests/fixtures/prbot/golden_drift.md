<!-- shape-pr-comment -->
## Shape data check

**Verdict: drift** - 1 source, 3 findings

### Source: orders

`shape diff` - **drift**

| Table | Column | Kind | Severity |
|---|---|---|---|
| orders | region | column\_removed | high |
| orders | amount | mean\_shift | medium |
| orders | note | range\_change | low |

---
<sub>Shape 0.0.0</sub>
