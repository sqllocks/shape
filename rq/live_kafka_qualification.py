"""Live Kafka smoke/throughput gate. Requires SHAPE_LIVE_KAFKA=1 and a broker."""
import os,json,time,uuid
if os.getenv("SHAPE_LIVE_KAFKA")!="1":raise SystemExit("live Kafka gate requires explicit opt-in")
from confluent_kafka import Producer,Consumer
bootstrap=os.environ["KAFKA_BOOTSTRAP_SERVERS"];topic=os.getenv("KAFKA_TOPIC","shape-qualification")
N=int(os.getenv("SHAPE_CONNECTOR_ROWS","100000"))
p=Producer({"bootstrap.servers":bootstrap});t=time.perf_counter()
for i in range(N):p.produce(topic,json.dumps({"id":i,"value":i%1000}).encode())
p.flush(30)
group="shape-"+uuid.uuid4().hex;c=Consumer({"bootstrap.servers":bootstrap,"group.id":group,"auto.offset.reset":"earliest","enable.auto.commit":False});c.subscribe([topic])
seen=set();deadline=time.time()+60
while len(seen)<N and time.time()<deadline:
 m=c.poll(1)
 if m is None:continue
 if m.error():raise RuntimeError(m.error())
 seen.add(json.loads(m.value())["id"])
c.commit(asynchronous=False);c.close();e=time.perf_counter()-t
assert len(seen)==N
print(json.dumps({"connector":"kafka","rows":N,"seconds":e,"rows_per_second":N/e,"unique":len(seen)}))
