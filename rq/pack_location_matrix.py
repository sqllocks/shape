import json
from shape.packs.address import *
from shape.packs.person import generate_person
from shape.location import *
out={"passed":[],"failed":[],"roadblocks":[]}
def ck(n,fn):
 try:fn();out["passed"].append(n)
 except Exception as e:out["failed"].append({"name":n,"error":repr(e)})
rows=[
 AddressReference("1 High St","Columbus","Franklin","OH","43215","US",39.96,-83.0,"America/New_York","a"),
 AddressReference("2 Main St","Dublin","Franklin","OH","43017","US",40.10,-83.11,"America/New_York","b"),
 AddressReference("3 State St","Erie","Erie","PA","16501","US",42.12,-80.08,"America/New_York","c")]
pack=AddressPack(rows,"v1")
def scopes():
 cases=[
  LocationScope.one(Location.state_scope("OH")),
  LocationScope.one(Location.county_scope("Franklin","OH")),
  LocationScope.one(Location.city_scope("Columbus","OH")),
  LocationScope.one(Location.zip("43215")),
  LocationScope.any(Location.city_scope("Columbus","OH"),Location.city_scope("Erie","PA")),
  LocationScope.weighted([(Location.city_scope("Columbus","OH"),9),(Location.city_scope("Erie","PA"),1)]),
  LocationScope((WeightedLocation(Location.state_scope("OH")),),(Location.city_scope("Dublin","OH"),))]
 for s in cases:
  for mode in ("geographic","street_synthetic","reference","exact_reference"):
   x=pack.generate(100,s,4,mode);assert len(x)==100
ck("location_scope_modes_include_exclude_weighted",scopes)
def person(): 
 x=[generate_person(i,9) for i in range(10000)];assert len({r["email"] for r in x})==10000 and x[123]==generate_person(123,9)
ck("person_pack_unique_deterministic",person)
# Resolver ambiguity/unknown
def resolver():
 r=LocationResolver([Location("US","OH","Franklin","Columbus","43215","id1"),Location("US","OH","Franklin","Dublin","43017","id2")],"v")
 assert r.resolve(Location.zip("43215")).canonical_id=="id1"
 try:r.resolve(Location.state_scope("OH"));raise AssertionError("ambiguity not rejected")
 except AmbiguousLocationError:pass
ck("location_resolver_fail_closed",resolver)
print(json.dumps(out))
