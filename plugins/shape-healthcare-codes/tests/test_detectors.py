import pyarrow as pa
from shape_healthcare_codes import detectors as d
from shape_healthcare_codes.npi import generate_synthetic_npis

from shape.plugins import kit

ICD = ["E11.9", "I10", "N18.30", "S72.001A", "Z00.00", "J45.909", "E119", "M54.50"] * 5
NDC = ["0002-3227-30", "12345-678-90", "12345-6789-1", "00002-3227-30"] * 10
HCPCS = ["J1100", "A4206", "E0100", "G0008", "V2020", "L1830"] * 8
MBI = ["1EG4-TE5-MK73", "1EG4TE5MK73", "2AC3-DE4-FG56"] * 10
MEMBER = ["ABC123456789", "XYZ987654321-01", "QRS000111222"] * 10


def test_kit_conformance_for_every_detector():
    npis = generate_synthetic_npis(60, seed=3) + ["1234567893"] * 5
    cases = [
        (d.Icd10Detector(), ICD, [["alice", "bob"], ["12345", "67890"], ["Seattle", "Austin"]]),
        (d.NdcDetector(), NDC, [["alice", "bob"], ["123456789", "987654321"]]),
        (
            d.NpiDetector(),
            npis,
            [["alice", "bob"], [str(1000000000 + i * 7919) for i in range(60)]],
        ),
        (d.HcpcsDetector(), HCPCS, [["alice", "bob"], ["12345", "23456"]]),
        (d.MbiDetector(), MBI, [["alice", "bob"], ["12345", "67890"]]),
        (d.MemberIdDetector(), MEMBER, [["alice", "bob"], ["12345", "67890"]]),
    ]
    for det, pos, neg in cases:
        kit.check_detector(det, positives=[pos], negatives=neg)


def test_labels_and_confidence():
    got = d.Icd10Detector().detect(pa.array(ICD), "dx_code")
    assert got is not None and got.label == "icd10" and got.confidence > 0.9
    assert d.NdcDetector().detect(pa.array(NDC), "x").label == "ndc"
    assert d.NpiDetector().detect(pa.array(generate_synthetic_npis(40)), "x").label == "npi"
    assert d.HcpcsDetector().detect(pa.array(HCPCS), "x").label == "hcpcs"
    assert d.MbiDetector().detect(pa.array(MBI), "x").label == "mbi"


def test_ambiguous_shapes_need_the_column_name():
    five = pa.array([f"{10000 + i * 37:05d}" for i in range(50)])
    assert d.CptDetector().detect(five, "zip") is None
    assert d.CptDetector().detect(five, "cpt_code") is not None
    assert d.CptDetector().detect(pa.array(["0001F", "0002F", "0100T"] * 10), "x") is not None
    bare = pa.array([f"0002{i:07d}"[:11] for i in range(30)])
    assert d.NdcDetector().detect(bare, "phone") is None
    assert d.NdcDetector().detect(bare, "ndc_code") is not None
    all3 = pa.array(["A12", "B34", "C56"] * 10)
    assert d.Icd10Detector().detect(all3, "code") is None
    assert d.MemberIdDetector().detect(pa.array(MEMBER), "member_id").confidence > 0.9
    assert d.MemberIdDetector().detect(pa.array(MEMBER), "sku").confidence < 0.7


def test_random_ten_digit_numbers_are_not_npis():
    nums = [str(1000000000 + (i * 2654435761) % 999999999) for i in range(200)]
    assert d.NpiDetector().detect(pa.array(nums), "x") is None


def test_integer_columns_are_read_as_strings():
    ints = pa.array([int(n) for n in generate_synthetic_npis(40)])
    assert d.NpiDetector().detect(ints, "rendering_npi") is not None


def test_the_installed_distribution_passes_the_plugin_kit_with_samples():
    from shape.plugins.kit import check_installed

    neg = [["alice", "bob"], ["12345", "67890"]]
    samples = {
        "shape.detectors:icd10": {"positives": [ICD], "negatives": neg},
        "shape.detectors:ndc": {"positives": [NDC], "negatives": neg},
        "shape.detectors:npi": {"positives": [generate_synthetic_npis(40)], "negatives": neg},
        "shape.detectors:hcpcs": {"positives": [HCPCS], "negatives": neg},
        "shape.detectors:cpt": {"positives": [["0001F", "0002F", "0100T"] * 10], "negatives": neg},
        "shape.detectors:mbi": {"positives": [MBI], "negatives": neg},
        "shape.detectors:member_id": {"positives": [MEMBER], "negatives": neg},
        "shape.commands:healthcare-codes": {"argv": ["list"]},
    }
    lines = check_installed("sqllocks-shape-healthcare-codes", samples)
    assert len(lines) == 8 and not any("shared rules only" in x for x in lines)
