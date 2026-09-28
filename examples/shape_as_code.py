import shape
rows=[{"id":1,"age":42},{"id":2,"age":37},{"id":3,"age":42}]
s=shape.profile(rows)
shape.save(s,"customer.shape",name="customer")
loaded=shape.load("customer.shape")
print(shape.query(loaded,'column("age").mean'))
data,report=shape.generate(loaded,1000,seed=42)
print(report)
