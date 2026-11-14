# Audience Q&A

**How is this different from masking tools like Tonic?**
Shape's workflow here doesn't copy production data: it ships statistics and adds quality
gates, then generates new rows. Masking workflows transform existing rows. Tool suites
vary; this is a workflow distinction, not a claim about every capability of Tonic.
A profile can still retain values: inspect and govern it before sharing.

**Is it open source?**
The core is open source; see the repository [LICENSE](../../../LICENSE).
A commercial edition exists. This demo does not require it.

**What works in Fabric today versus what's planned?**
The repository ships Spark profiling notebooks, an Environment definition and pipeline
gate scaffolds, plus Python domain-generation notebooks. The local APIs and CLI in this
kit are executable and verified by verify_demo.sh. Live tenant behavior remains
**Fabric-only, awaiting owner rehearsal**. Explicit batch-date checks and tuned drift
in the talk use the workspace notebook customization in DEMO.md; they are not claimed
as shipped notebook parameters. Profile-to-dev generation uses a separate Spark cell.
Do not promise a future release date, automatic dashboard protection, or tenant support
beyond what the owner has actually validated. Further integration work is not a demo dependency.

**Can every notebook use the Environment?**
No. Attach it to Spark notebooks. The shipped Python generation notebook uses inline
installation and cannot attach an Environment. This talk uses Spark for the Environment
path and labels the Python notebook as an alternative, not the same execution path.

**Does a notebook returning passed:false stop a pipeline?**
Only if orchestration reads it. The If Condition routes false to Fail and true to the
business load. A green Notebook activity by itself is insufficient. The load must sit
behind the gate, with refresh depending on successful load completion.

**Why pass --as-of rather than use today's date?**
Replays need the batch's business date. Day 2 is evaluated as December 5 and catches
November 25 rows even if this presentation happens on another date. Relative bounds
are inclusive calendar-day bounds; the kit defaults to UTC. Set contract timezone
explicitly for a different business-day boundary.

**Is regex evidence enough to prove all rows conform?**
Use exact source data with the rule, as this demo does via --data / data=. A limited
profile may not contain enough evidence. Distinguish a violation from an unevaluable
rule; do not treat missing evidence as a pass. Nullability is separate from regex.

**Is a realistic synthetic copy identical, private, or relationally complete?**
No such guarantee follows from statistical similarity. Inspect the reconstruction plan
and validate generated data for the intended use. This kit reconstructs one orders
table; it does not demonstrate cross-table customer/order relationships. Its fixtures
contain no real people, but arbitrary production profiles can expose sensitive values.

**How fast is it?**
This kit makes no throughput, memory, or Fabric latency claim. The earlier
[NUMBERS.md](../shape-v1/NUMBERS.md) is the only permitted historical performance source;
its measurements have their own hardware and exact-mode scope and must not be relabelled
as Fabric results. Run representative workloads on the actual capacity before sizing.

**Can I use the distributed notebook for the contract gate?**
Not as demonstrated here. The shipped distributed path has different evidence and
contract limitations. This small-table demo uses driver-side profiling; check the
[Fabric runbook](../../../integrations/fabric/RUNBOOK.md) before choosing a scale path.

**What if the network fails?**
All four stages have Parquet fallbacks. After bootstrap, verify_demo.sh executes with
UV_OFFLINE=1 from the populated local environment. This verifies offline dependency
resolution, not a firewall-level network isolation test.
