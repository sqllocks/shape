# Day-of readiness — owner checklist

Unchecked items are requirements to verify, not completed-work claims. If the Fabric
rehearsal is incomplete, deliver the offline path and label the Fabric walkthrough.

- [ ] Confirm early-December date, guest start, title and 45-minute content / Q&A split
      with the host. Regular show slot: Fridays, 8:00–9:30 AM ET.
- [ ] Workspace shape_friday accessible; source shape_demo and separate shape_dev exist;
      presenter has read/write rights and active capacity.
- [ ] Three committed synthetic CSV fixtures uploaded under Files/fabric-friday;
      orders_day1, orders_day2 and customers source Delta tables populated.
- [ ] Contract uploaded under Files/contracts/friday_orders.json; fixture batch dates
      are December 4 and 5 regardless of the broadcast date.
- [ ] Record tested checkout SHA: __________; installed Shape version: __________;
      Python / Spark / numpy / pyarrow versions: __________. Match local and Fabric
      features; do not assume an older index package supports the new rules.
- [ ] Environment shape-env has the tested wheel and required dependency wheels;
      Runtime 2.0; Full publish finished; fresh Spark session started.
- [ ] Imported shape_profile_spark copy and separate generation Spark notebook attached
      to shape-env. Source profiler defaults to shape_demo; generation defaults to
      shape_dev. Confirm attachments visually before writes.
- [ ] Workspace check/diff customization in DEMO.md applied; day-1 baseline saved;
      day 1 passes and day 2 fails with the three named violations and tuned drift.
- [ ] shape_gate_spark imported and bound to the workspace notebook copy. If Condition
      reads passed; true branch contains load; false branch fails; dashboard refresh
      depends on successful load. Confirm day-2 load did not run.
- [ ] Profile reconstruction plan reviewed; dev generation produces the separate table.
      Do not run Python notebook inline install cells for the Environment path.
- [ ] Record Fabric rehearsal date, operator and output locations: __________.
      No Fabric timing claim without its own committed measurement record.
- [ ] Fallback laptop checkout path: __________/shape. Populate cache with make bootstrap;
      run bash docs/talks/fabric-friday-2026-12/verify_demo.sh there without network access.
- [ ] Record local verified checkout/run date: __________. Keep generated day1.html,
      pass.json, fail.json, drift.json and plan.json for screen-share fallback; run
      DEMO.md blocks manually in a persistent scratch folder to retain these outputs.
- [ ] Terminal readable, correct browser tabs, screen-share tested, notifications off;
      source data and profiles are synthetic and visibly labelled.
- [ ] Owner supplies viewer offer before show; until then show only:

[Premium trial event code — Owner to create]
