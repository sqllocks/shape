# Owner browser handoff

Run these steps yourself in Fabric. Do not send credentials, tokens, connection strings,
identity IDs, usernames, or full model values back to chat. The prepared notebook prints
versions, aggregate counts and dimensions; the endpoint connection string, Query URI
host and database name all use hidden prompts.

1. Open the **Shape Test** workspace. Import `f12-owner-qualification.ipynb` as
   **f12-owner-qualification**. Choose the **Python** experience and Python **3.12**.
   Keep the default **2 vCore / 16 GB** configuration; do not attach an Environment,
   choose PySpark, or increase vCores. Record the runtime label and resource setting
   shown in the UI as text alongside the output.
2. In **Resources > builtin**, upload the three supplied 0.9.4 wheels: core (the
   `py3-none-any` wheel), Fabric, and Event Hubs. Run the first cell for preinstall
   versions, then the `%pip` cell. If Fabric asks to restart the session, do so,
   then run the after-install cell. Copy those cell outputs, excluding any personal
   paths, identity details, or tokens in installation diagnostics.
3. Run the memory cell and wait for both row counts. Copy every generated JSON line,
   including child exit codes and errors. These are a ten-column Shape-generated
   workload at the public plan's row counts, not the D3 benchmark fixture.
4. Run the semantic-model cell. It targets existing `shape_sm` read-only and invokes
   the repository's existing live test body with your notebook identity. Copy its
   summary JSON, including failure if it fails. Source inspection confirms the Shape
   call path lists metadata, evaluates read-only DAX, and opens TOM with `readonly=True`;
   it does not request a model update. This is a code audit, not a live execution proof.
5. Pause before the final notebook cell. In Shape Test create **f12-eventhouse**;
   retain its automatically created KQL database only if its name starts `f12-`.
   In its KQL explorer, create the stream target table before configuring the destination.
   If Fabric requires a saved KQL queryset, name the new item **f12-queries**:

   ```kusto
   .create table ['f12-events'] (id:long, marker:string, _shape_table:string, _shape_seq:long)
   ```

   Create **f12-eventstream**, add a **Custom endpoint** source named **f12-input**,
   and add an **Eventhouse** destination to the new f12 database; select the **existing f12-events table**
   you created above. Select JSON input and map `id`, `marker`, `_shape_table`, and `_shape_seq` to
   the matching target columns (JSON paths `$.id`, `$.marker`, `$._shape_table`,
   `$._shape_seq`). Publish the topology, and complete **Activate
   ingestion** for the destination before sending any events. Confirm the destination
   is active; record that action as text. If the UI cannot create these names or
   offers different controls, stop and report that text rather than reuse existing items.
6. Copy the custom endpoint's Event Hubs connection string locally. Run the final
   notebook cell once. Enter the connection string only into its hidden prompt;
   enter the new Eventhouse Query URI host and the new f12 database name in their
   hidden prompts. Do not copy those connection details to chat. The cell sends
   separate emitter and stream-writer batches and a direct Eventhouse-writer control.
   Copy `events_sent` and `events_landed` JSON. The cell polls both the Eventstream
   and direct-writer counts for visibility; preserve a timeout or mismatch as observed.
   The direct writer uses streaming ingestion, whose rows may take seconds to appear.
   Counts must be observed, not assumed.
   If `getToken("kusto")` is unavailable, report the failure; do not supply tokens.
7. In the Eventhouse KQL explorer, independently run:

   ```kusto
   ['f12-events']
   | where marker == 'f12-live'
   | summarize raw_rows=count() by _shape_table
   ```

   ```kusto
   ['f12-events']
   | where marker == 'f12-live'
   | summarize by _shape_table, _shape_seq
   | summarize unique_rows=count() by _shape_table
   ```

   ```kusto
   ['f12-direct'] | count
   ```

   Copy the aggregate results. Expected values in the notebook are assertions to
   compare against the observations, not pre-recorded measurements. At-least-once
   delivery can produce more raw events than unique events; retain both counts.
8. Delete only **f12-eventstream**, **f12-eventhouse**, its f12 KQL database and
   **f12-owner-qualification**, **f12-queries** if created, and any other f12 items
   you created for this run.
   Stop the notebook session first. Confirm the f12 items are gone. Send the output,
   runtime/resource text, Activate-ingestion confirmation and cleanup confirmation.

If a long cell fails, retain its emitted JSON and report the visible error category
without personal paths or identity details. A failed measurement remains a result.
