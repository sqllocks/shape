from shape.privacy import release_for


def test_release_policy_removes_values_and_suppresses_small_cohort():
    s = {
        "rows": 100,
        "columns": {
            "email": {
                "kind": "text",
                "count": 100,
                "topk": [["a@x.com", 9]],
                "examples": ["a@x.com"],
            },
            "rare": {"kind": "text", "count": 2, "topk": [["secret", 2]]},
        },
    }
    r = release_for(s, {"email": "PII", "rare": "TOP_SECRET"}, "PUBLIC", 5)
    raw = str(r.shape)
    assert "a@x.com" not in raw and "secret" not in raw and r.shape["columns"]["rare"]["suppressed"]
