"""Parallel columnar evidence execution for mixed streaming batches."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from shape.capture import capture_columns
from shape.profile.text_vectorized import profile_text_semantic

from .platinum import (
    MissingnessPairEvidence,
    covariance_batch,
    geo_grid_batch,
    hashed_dependency_batch,
    relational_batch,
    temporal_batch,
    update_missingness_batch,
)


class FullEvidenceEngine:
    def __init__(self, workers=3):
        self._pool = ThreadPoolExecutor(max_workers=workers)

    def close(self):
        self._pool.shutdown(wait=True)

    def process(
        self,
        numeric_columns,
        text_column,
        *,
        fk_name="fk",
        parent_count=None,
        lat_name="latitude",
        lon_name="longitude",
        dependency=None,
        time_name="id",
    ):
        f_profile = self._pool.submit(capture_columns, numeric_columns)
        f_text = self._pool.submit(profile_text_semantic, text_column)

        def relationships():
            out = {}
            list(numeric_columns)
            if "value" in numeric_columns and time_name in numeric_columns:
                out["covariance"] = covariance_batch(
                    numeric_columns[time_name], numeric_columns["value"]
                )
            if fk_name in numeric_columns and parent_count is not None:
                out["relational"] = relational_batch(numeric_columns[fk_name], parent_count)
            if lat_name in numeric_columns and lon_name in numeric_columns:
                out["geo"] = geo_grid_batch(
                    numeric_columns[lat_name], numeric_columns[lon_name], 0.01, 4096
                )
            if time_name in numeric_columns:
                out["temporal"] = temporal_batch(numeric_columns[time_name])
            if dependency:
                a, b = dependency
                out["dependency"] = hashed_dependency_batch(
                    numeric_columns[a], numeric_columns[b], 64
                )
            if "value" in numeric_columns and lat_name in numeric_columns:
                out["missingness"] = update_missingness_batch(
                    MissingnessPairEvidence(), numeric_columns["value"], numeric_columns[lat_name]
                )
            return out

        f_rel = self._pool.submit(relationships)
        profile = f_profile.result()
        text, semantic = f_text.result()
        return {"profile": profile, "text": text, "semantic": semantic, **f_rel.result()}
