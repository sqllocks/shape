"""REL-092: synthetic replacements retain the reference data's shape."""

import hashlib
import json
import re

import pyarrow as pa
from shape_domains.capital_markets import CapitalMarketsDomain
from shape_domains.healthcare import HealthcareDomain


def test_procedure_reference_is_synthetic_with_unchanged_numeric_distribution():
    table = HealthcareDomain().definition().reference_data["cpt_codes"]
    assert table.schema == pa.schema(
        [
            ("code", pa.string()),
            ("description", pa.string()),
            ("charge", pa.float64()),
            ("weight", pa.float64()),
        ]
    )
    assert table.num_rows == 49
    assert table["code"].to_pylist() == [f"SP-{i:02d}" for i in range(1, 50)]
    assert all(
        re.fullmatch(r"Synthetic procedure [0-9]{2}", x) for x in table["description"].to_pylist()
    )
    numeric = json.dumps(table.select(["charge", "weight"]).to_pylist(), separators=(",", ":"))
    assert hashlib.sha256(numeric.encode()).hexdigest() == (
        "ee2217bc85b2536092e106b0260c724dfbf1d38ec11d048c6ccfdd375630a9e4"
    )


def test_sector_taxonomy_and_company_labels_are_synthetic_and_linked():
    provider = CapitalMarketsDomain().definition().reference_data
    sectors = provider["gics_sectors"]
    assert sectors.num_rows == 11
    assert sectors.schema == pa.schema(
        [
            ("sector_id", pa.int64()),
            ("sector_name", pa.string()),
            ("sector_code", pa.string()),
            (
                "industries",
                pa.list_(pa.struct([("industry_id", pa.int64()), ("industry_name", pa.string())])),
            ),
        ]
    )
    assert sectors["sector_id"].to_pylist() == list(range(1, 12))
    assert sectors["sector_code"].to_pylist() == [f"S{chr(65 + i)}" for i in range(11)]
    assert [len(x) for x in sectors["industries"].to_pylist()] == [6, 6, 6, 7, 5, 11, 6, 2, 5, 2, 5]
    assert all(x.startswith("Synthetic sector ") for x in sectors["sector_name"].to_pylist())
    assert all(
        industry["industry_name"].startswith("Synthetic industry ")
        for group in sectors["industries"].to_pylist()
        for industry in group
    )
    assert [
        industry["industry_id"] for group in sectors["industries"].to_pylist() for industry in group
    ] == list(range(1, 62))
    companies = provider["sp500_constituents"]
    assert companies.num_rows == 123
    assert len(set(companies["sector"].to_pylist())) == 11
    assert len(set(companies["industry"].to_pylist())) == 45
    assert set(companies["sector"].to_pylist()) <= set(sectors["sector_name"].to_pylist())
    assert all(x.startswith("Synthetic industry ") for x in companies["industry"].to_pylist())
