# Exact F12 API setup (prepared, not yet tenant-qualified)

Use the existing workspace reference in `f12_agent.py`. Acquire a Fabric token from
the configured service principal in memory; set Authorization internally. Never use
verbose curl, echo a payload/response, pass tokens on a command line, or save secret
responses. The helper is the executable form of these calls; it handles 201/202 and
same-origin operation polling, saves created item IDs in a private ledger, and emits
only safe status receipts. Stop if any f12 item already exists; do not adopt it.

## Eventhouse, database, schema

1. `POST https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/eventhouses`

   ```json
   {"displayName":"f12-eventhouse"}
   ```

2. `POST https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/kqlDatabases`

   ```python
   # E is only the returned ID of the Eventhouse just created.
   from f12_agent import database_body
   payload = database_body(E)
   # api.call("POST", BASE + "kqlDatabases", payload); never print payload/response.
   ```

   `database_body(E)` constructs this exact body, Base64-encoding the two UTF-8 parts:

   ```python
   {
     "displayName": "f12-kql",
     "definition": {"parts": [
       {"path":"DatabaseProperties.json", "payloadType":"InlineBase64",
        "payload": b64(json.dumps({"databaseType":"ReadWrite", "parentEventhouseItemId":E}))},
       {"path":"DatabaseSchema.kql", "payloadType":"InlineBase64", "payload":b64(schema)}
     ]}
   }
   ```

   The exact decoded schema is:

   ```kusto
   .create table ['f12-events'] (id:long, marker:string, _shape_table:string, _shape_seq:long)
   .create-or-alter table ['f12-events'] ingestion json mapping 'f12-json' "[{\"column\":\"id\",\"Properties\":{\"path\":\"$.id\"}},{\"column\":\"marker\",\"Properties\":{\"path\":\"$.marker\"}},{\"column\":\"_shape_table\",\"Properties\":{\"path\":\"$._shape_table\"}},{\"column\":\"_shape_seq\",\"Properties\":{\"path\":\"$._shape_seq\"}}]"
   ```

The database definition applies the table/mapping during creation, requiring no KQL
UI step. The `f12-json` mapping documents the exact JSON field-to-column contract.
Eventstream **ProcessedIngestion provisions its own ingestion** from the JSON
serialization and table schema; this mode does not accept a `mappingRuleName` or
use the named `f12-json` mapping. Do not claim it does. Its managed mapping must
preserve these same four names/paths; matching observed grouped counts is the live
check, not the existence of the audit mapping. The separate direct-writer control
uses Shape's own generated table/mapping, also not `f12-json`.

References: [Create Eventhouse](https://learn.microsoft.com/en-us/rest/api/fabric/eventhouse/items/create-eventhouse),
[Create KQL Database](https://learn.microsoft.com/en-us/rest/api/fabric/kqldatabase/items/create-kql-database),
[KQL database definition and schema](https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/kql-database-definition),
[deployment by definition](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/eventhouse-deploy-with-fabric-api).

## Eventstream and the selected mode

3. `POST https://api.fabric.microsoft.com/v1/workspaces/{workspaceId}/items`

   ```python
   # D is only the ID of the f12-kql database just created.
   from f12_agent import eventstream_body
   payload = eventstream_body(D)
   # api.call("POST", BASE + "items", payload); never print payload/response.
   ```

   The body is `{"displayName":"f12-eventstream","type":"Eventstream",` followed
   by `"definition":{"parts":[{"path":"eventstream.json","payloadType":"InlineBase64",`
   and `"payload":b64(json.dumps(topology))}]}}`, using this topology:

   ```python
   topology = {
     "sources": [{"name":"f12-input", "type":"CustomEndpoint", "properties":{}}],
     "streams": [{"name":"f12-stream", "type":"DefaultStream", "properties":{},
                  "inputNodes":[{"name":"f12-input"}]}],
     "operators": [],
     "destinations": [{"name":"f12-destination", "type":"Eventhouse",
       "inputNodes":[{"name":"f12-stream"}],
       "properties": {
         "dataIngestionMode":"ProcessedIngestion",
         "workspaceId":workspaceId, "itemId":D,
         "databaseName":"f12-kql", "tableName":"f12-events",
         "inputSerialization":{"type":"Json","properties":{"encoding":"UTF8"}}
       }}],
     "compatibilityLevel":"1.1"
   }
   ```

We chose **ProcessedIngestion** because it matches the handoff's JSON ingestion
into an existing four-column table, without an externally provisioned Kusto data
connection. Do not mix modes. **DirectIngestion** instead needs `connectionName`
and `mappingRuleName` for an existing Kusto connection/mapping; a named mapping
alone is insufficient. The wrong property combination can be accepted yet leave
Warning and ingest no rows. A successful create request proves neither readiness
nor delivery. See [mode-specific contracts](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/eventstream-rest-api)
and [Eventstream definition](https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/eventstream-definition).

## API activation and pre-send guard

4. `GET /v1/workspaces/{workspaceId}/eventstreams/{eventstreamId}/topology` finds
   the IDs of **f12-input** and **f12-destination**. Then:

   ```text
   GET /v1/workspaces/{workspaceId}/eventstreams/{eventstreamId}/destinations/{destinationId}
   ```

   Reject Warning immediately. Reject mismatched mode, item, table or JSON format.
   If not Running, the setup helper attempts:

   ```text
   POST /v1/workspaces/{workspaceId}/eventstreams/{eventstreamId}/destinations/{destinationId}/resume
   ```

   ```json
   {"startType":"Now"}
   ```

   Poll the GET until source and destination are Running with no destination error.
   Resume's 200 alone is insufficient. Recheck in the final cell immediately before
   emitting. A Warning, non-Running state, error or unsupported API prevents sending.
   Activation is therefore **API-capable**; portal **Activate ingestion** remains
   an explicit Owner fallback only if this route fails in the tenant, followed by
   the same API readiness guard. Do not bypass the guard after manual activation.

   Sources: [Get topology](https://learn.microsoft.com/en-us/rest/api/fabric/eventstream/topology/get-eventstream-topology),
   [Get destination](https://learn.microsoft.com/en-us/rest/api/fabric/eventstream/topology/get-eventstream-destination),
   [Resume destination](https://learn.microsoft.com/en-us/rest/api/fabric/eventstream/topology/resume-eventstream-destination),
   [pause/resume as deactivate/activate](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/pause-resume-data-streams).

## Connection retrieval, run and cleanup

5. Read only in memory:

   ```text
   GET /v1/workspaces/{workspaceId}/eventstreams/{eventstreamId}/sources/{sourceId}/connection
   GET /v1/workspaces/{workspaceId}/kqlDatabases/{kqlDatabaseId}
   ```

   The first returns `accessKeys.primaryConnectionString` and `eventHubName` for a
   CustomEndpoint. Append EntityPath only if absent. The second supplies
   `properties.queryServiceUri` and the new item's display name. These values are
   never logged or passed as job parameters. The notebook fetches them itself,
   or reads the existing vault bundle; it asserts the database prefix and verifies
   host/name against the new database metadata before connecting. It also compares
   the SAS Endpoint/EntityPath against the expected namespace and Event Hub name.
   API metadata binds these to the new source; without that API the explicit Owner
   fallback attests the source association with hidden metadata or the vault bundle.
   A credential for a different endpoint must never be sent as a test.
   [Source connection API](https://learn.microsoft.com/en-us/rest/api/fabric/eventstream/topology/get-eventstream-source-connection),
   [KQL database properties](https://learn.microsoft.com/en-us/rest/api/fabric/kqldatabase/items/get-kql-database).

6. Create the hash-verified, rendered notebook using `POST /v1/workspaces/{workspaceId}/items`
   with `type: Notebook`, `displayName: f12-owner-qualification` and the InlineBase64
   `notebook-content.ipynb` part in `definition.format: ipynb`. Then:

   ```text
   POST /v1/workspaces/{workspaceId}/notebooks/{notebookId}/jobs/execute/instances?beta=false
   ```

   ```python
   {
     "executionData":{"compute":"Jupyter","computeConfiguration":{"numCores":2}},
     "parameters":[
       {"name":"F12_NONINTERACTIVE","type":"Boolean","value":True},
       {"name":"F12_EVENTSTREAM_ID","type":"Text","value":eventstreamId},
       {"name":"F12_KQL_DATABASE_ID","type":"Text","value":kqlDatabaseId}
     ]
   }
   ```

   The definition's kernel is Python 3.12, and no `attachedEnvironment` or
   `defaultLakehouse` is sent. Respect Retry-After and poll the Location only on the
   Fabric API origin. Failures are retained, without printing remote error text.
   [Jupyter request schema](https://learn.microsoft.com/en-us/rest/api/fabric/notebook/background-jobs/run-on-demand-notebook),
   [notebook exit values](https://learn.microsoft.com/en-us/fabric/data-engineering/notebook-public-api).

7. Once terminal, the helper deletes in reverse creation order using
   `DELETE /v1/workspaces/{workspaceId}/items/{itemId}`, after checking each recorded
   item still has the expected type and f12 prefix. No secret-response bodies are
   written to the ledger. Unknown pre-existing f12 items are never adopted/deleted.
