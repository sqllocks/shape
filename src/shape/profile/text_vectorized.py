"""Vectorized/dictionary-encoded text profiling and semantic detection."""

from __future__ import annotations


def profile_text_semantic(values):
    import numpy as np

    a = np.asarray(values)
    if a.dtype.kind not in "US":
        a = a.astype(str)
    n = int(a.size)
    if not n:
        return {"count": 0, "null_count": 0, "distinct_estimate": 0, "topk": []}, ()
    unique, counts = np.unique(a, return_counts=True)
    lengths = np.char.str_len(unique)
    weighted_len = float(np.dot(lengths.astype(np.float64), counts.astype(np.float64)) / n)
    order = np.argsort(counts)[::-1][:10]
    # Classify dictionary values once, then weight by occurrence count.
    has_at = np.char.find(unique, "@") >= 1
    has_dot = np.char.find(unique, ".") >= 1
    email_weight = int(counts[has_at & has_dot].sum())
    stripped = np.char.replace(np.char.replace(unique, "-", ""), " ", "")
    digits = np.char.isnumeric(stripped)
    lens = np.char.str_len(stripped)
    numeric_id_weight = int(counts[digits & (lens == 9)].sum())
    sem = []
    if email_weight / n >= 0.2:
        sem.append(("email", email_weight / n))
    if numeric_id_weight / n >= 0.2:
        sem.append(("numeric_identifier", numeric_id_weight / n))
    prof = {
        "count": n,
        "null_count": 0,
        "min_length": int(lengths.min()),
        "max_length": int(lengths.max()),
        "mean_length": weighted_len,
        "distinct_estimate": int(unique.size),
        "topk": [(str(unique[i]), int(counts[i])) for i in order],
    }
    return prof, tuple(sem)


def profile_text_array(values):
    return profile_text_semantic(values)[0]


def semantic_detect_array(values):
    return profile_text_semantic(values)[1]
