"""Live Event Hubs send/receive gate. Requires explicit opt-in and credentials."""
import os,json,time
if os.getenv("SHAPE_LIVE_EVENTHUBS")!="1":raise SystemExit("live Event Hubs gate requires explicit opt-in")
from azure.eventhub import EventHubProducerClient,EventData,EventHubConsumerClient
cs=os.environ["AZURE_EVENTHUB_CONNECTION_STRING"];name=os.environ["AZURE_EVENTHUB_NAME"];N=int(os.getenv("SHAPE_CONNECTOR_ROWS","10000"))
producer=EventHubProducerClient.from_connection_string(cs,eventhub_name=name);t=time.perf_counter()
batch=producer.create_batch()
for i in range(N):
 e=EventData(json.dumps({"id":i,"value":i%1000}))
 try:batch.add(e)
 except ValueError:producer.send_batch(batch);batch=producer.create_batch();batch.add(e)
if len(batch):producer.send_batch(batch)
producer.close();seen=set()
consumer=EventHubConsumerClient.from_connection_string(cs,consumer_group="$Default",eventhub_name=name)
deadline=time.time()+90
def on_event(partition,event):
 seen.add(json.loads(event.body_as_str())["id"]);partition.update_checkpoint(event)
 if len(seen)>=N or time.time()>deadline:consumer.close()
consumer.receive(on_event=on_event,starting_position="-1",max_wait_time=5)
assert len(seen)>=N
e=time.perf_counter()-t
print(json.dumps({"connector":"eventhubs","rows":N,"seconds":e,"rows_per_second":N/e,"unique":len(seen)}))
