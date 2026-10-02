from shape.privacy import release_for

shape = {
    "rows": 100,
    "columns": {"email": {"kind": "text", "count": 100, "topk": [["person@example.com", 20]]}},
}
decision = release_for(shape, {"email": "PII"}, target="PUBLIC")
assert "person@example.com" not in str(decision.shape)
print(decision.removed)
