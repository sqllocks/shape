# Run of show — 45 minutes plus Q&A

Guest: Jonathan Stewart. Proposed title is in [ABSTRACT.md](ABSTRACT.md).
Fabric Friday airs Fridays 8:00–9:30 AM ET; the early-December date and guest segment
start are owner confirmations. Times below are elapsed from the guest introduction,
not promises about the host's clock. Reserve 15 minutes for Q&A within the host's slot.
Use [DEMO.md](DEMO.md) and its numbered offline blocks as the operator script.

| Elapsed | Screen | Say / action | Fallback |
|---|---|---|---|
| 00–02 | Title and four demo promises | Introduce Jonathan and the workflow from profile to dev table. | Read abstract with no slides. |
| 02–05 | Source/dev Lakehouse diagram drawn on screen | Production here is an invented stand-in. Moving rows creates access and refresh work. | Describe source → gate → load → dashboard, with profile → dev generation. |
| 05–07 | Seeded files and provenance paragraph | Show 240 orders, 60 customers, two batch dates. Explain deliberate faults. | Open the committed generator locally. |
| 07–09 | Environment and Spark notebook attachments | Environment is published before the show. Python notebooks differ. | Read installed local version; disclose Fabric unavailable. |
| 09–12 | Demo 1, day-1 notebook | Profile the orders Lakehouse table and inspect the report. | Offline profile block and day1.html. |
| 12–15 | Customer report and saved baseline | Point at category frequencies; save day-1 artifact path. Treat profiles like source data. | customers.shape and local report. |
| 15–18 | Demo 2 contract JSON | Explain whole-value regex, nullability and explicit relative bounds. | Show local orders.json. |
| 18–21 | Day-1 gate output | Pass with December 4 fixture date. Notebook exit JSON controls pipeline branching. | Offline pass block, pass.json. |
| 21–24 | Day-2 gate output | December 5 load contains November 25 rows. Explain three violation lines. | Offline fail block, fail.json. |
| 24–27 | If Condition, Fail branch and load activity | Only the true branch loads data. An activity completing is not a contract pass. | Local exit 0/1/2 and pipeline diagram; do not claim a Fabric run. |
| 27–30 | Demo 3 baseline/current drift | New status values and amount shift precede dashboard damage. | Offline diff and drift.json. |
| 30–33 | Tuned threshold and dashboard dependency | Explain 0.25 standard deviations as demo policy; calibrate on known good batches. | Read the checked change kinds from verifier output. |
| 33–36 | Demo 4 reconstruction plan | Explain preserved, approximate and unmodelled behavior before generating. | Offline plan.json; never promise exact reconstruction. |
| 36–39 | Separate dev Lakehouse, generated table | Generate from the day-1 profile with seed 1207. Show the destination. | Offline generation block and dev Parquet. |
| 39–42 | Shipped notebook alternatives | Distinguish profile reconstruction from authored retail-domain generation; explain Python/Environment limitation. | Open shipped notebook source without running installs. |
| 42–45 | Workflow recap and viewer offer placeholder | Give repo/runbook location, privacy caveat, open-source core, and invite questions. Owner supplies event offer later. | Show READINESS.md placeholder; never invent a code. |
| 45–60 | QA.md and relevant output | Audience Q&A; acknowledge tenant and scale limits honestly. | Answer from documented evidence; log unanswered questions for owner. |

At each checkpoint, move to the offline equivalent if Fabric does not respond promptly.
Do not spend the content slot repairing authentication or publishing an Environment.
If offline execution also fails, show only previously verified artifacts labelled with
checkout and run date; say that the live execution failed. No timing claims are needed.
