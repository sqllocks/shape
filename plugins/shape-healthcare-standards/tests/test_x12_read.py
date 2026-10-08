"""W5-07: the X12 reader (generic segments, 835 loop tables, envelope checks, round trip)."""

from __future__ import annotations

import sys
from pathlib import Path

import pyarrow as pa
import pytest
from shape_healthcare_standards.sources import X12Source
from shape_healthcare_standards.testing import sample_tables
from shape_healthcare_standards.x12.read import parse_x12
from shape_healthcare_standards.x12.sink import X12Remittance835Sink

import shape
from shape.plugins import kit

sys.path.insert(0, str(Path(__file__).parent / "x12"))
from x12_helpers import ALL_SINKS, run  # noqa: E402

ISA = (
    "ISA*00*          *00*          *ZZ*SYNTHSENDER    *ZZ*SYNTHRECEIVER  "
    "*240101*1200*^*00501*{ic}*0*T*:~"
)
HEADER_835 = "GS*HP*SYNTHSENDER*SYNTHRECEIVER*20240101*1200*{gc}*X*005010X221A1~"


def envelope(*sets: list[str], ic: str = "000000001", gc: str = "1", gs: str | None = None) -> str:
    """A small valid interchange around ``sets`` (lists of body segments), counts computed."""
    lines = [ISA.format(ic=ic), (gs or HEADER_835).format(gc=gc)]
    for n, body in enumerate(sets, start=1):
        ctl = f"{n:04d}"
        lines.append(f"ST*835*{ctl}~")
        lines.extend(body)
        lines.append(f"SE*{len(body) + 2}*{ctl}~")
    lines.append(f"GE*{len(sets)}*{gc}~")
    lines.append(f"IEA*1*{ic}~")
    return "".join(lines)


BODY = [
    "BPR*I*10*C*NON~",
    "TRN*1*0000000001*1999999999~",
    "N1*PR*PAYER*XV*12345~",
    "N3*1 MAIN ST~",
    "N1*PE*PROVIDER*XX*1999999999~",
    "LX*1~",
    "CLP*C1*1*100*80**12*PCN1*11*1~",
    "NM1*QC*1*DOE*JANE****MI*M1~",
    "CAS*PR*1*20~",
    "SVC*HC:99213*100*80**1~",
    "DTM*472*20240101~",
    "CAS*CO*45*15~",
    "CAS*PR*2*5~",
    "CLP*C2*1*50*50**12*PCN2*11*1~",
    "SVC*HC:99214:25*50*50**1~",
    "SVC*HC:99215*10*10**1~",
    "PLB*1999999999*20240101*FB:0000000001*0~",
]


def read(tmp_path: Path, text: str, **options):
    p = tmp_path / "t.x12"
    p.write_text(text, encoding="ascii", newline="")
    return X12Source().read_tables(str(p), **options)


def rows(table: pa.Table) -> list[dict]:
    return table.to_pylist()


# -- generic segments --------------------------------------------------------------------------


def test_every_segment_is_a_row_with_its_control_numbers(tmp_path):
    t = read(tmp_path, envelope(BODY))["x12_segment"]
    assert t.column_names == [
        "interchange_control",
        "group_control",
        "transaction_control",
        "position",
        "segment_id",
        "loop_id",
        "elements",
    ]
    r = rows(t)
    assert [x["segment_id"] for x in r][:4] == ["ISA", "GS", "ST", "BPR"]
    assert [x["position"] for x in r] == list(range(1, len(r) + 1))
    assert {x["interchange_control"] for x in r} == {"000000001"}
    assert r[0]["group_control"] is None and r[0]["transaction_control"] is None
    assert r[1]["group_control"] == "1" and r[1]["transaction_control"] is None
    assert r[2]["transaction_control"] == "0001"
    assert r[-1]["segment_id"] == "IEA" and r[-1]["group_control"] is None
    svc = next(x for x in r if x["segment_id"] == "SVC")
    assert svc["elements"] == ["HC:99213", "100", "80", "", "1"]  # component separators kept
    assert r[0]["elements"][-1] == ":" and len(r[0]["elements"]) == 16


def test_the_sinks_835_round__trips_to_the_same_segments(tmp_path):
    (path,) = run(X12Remittance835Sink, sample_tables(), tmp_path)
    raw = path.read_text(encoding="ascii")
    got = rows(X12Source().read_tables(str(path))["x12_segment"].select(["segment_id", "elements"]))
    rebuilt = [x["segment_id"] + "".join("*" + e for e in x["elements"]) for x in got]
    assert rebuilt == raw.rstrip("~").split("~")
    assert [x["segment_id"] for x in got].count("CLP") >= 1


@pytest.mark.parametrize("sink_cls", ALL_SINKS)
def test_every_writer_output_reads_strictly(sink_cls, tmp_path):
    (path,) = run(sink_cls, sample_tables(), tmp_path)
    tables = X12Source().read_tables(str(path), strict=True)
    ids = tables["x12_segment"].column("segment_id").to_pylist()
    assert ids[0] == "ISA" and ids[-1] == "IEA" and tables["x12_problems"].num_rows == 0


def test_delimiters_come_from_the_isa_segment(tmp_path):
    text = envelope(BODY).replace("*", "|").replace("~", "\n").replace("^", "!")
    text = text.replace("|:\n", "|>\n", 1).replace("HC:", "HC>")
    # ISA16 is now '>' and the segment terminator is a newline
    got = read(tmp_path, text.replace("|:\n", "|>\n"))["x12_segment"]
    svc = next(x for x in rows(got) if x["segment_id"] == "SVC")
    assert svc["elements"][0] == "HC>99213"
    assert got.num_rows == len(BODY) + 6


def test_newlines_between_segments_are_ignored(tmp_path):
    text = envelope(BODY).replace("~", "~\r\n")
    t = read(tmp_path, text)["x12_segment"]
    assert t.num_rows == len(BODY) + 6


def test_several_interchanges_groups_and_sets(tmp_path):
    first = envelope(BODY, BODY[:5], ic="000000001", gc="7")
    second = envelope(BODY[:5], ic="000000002", gc="9")
    t = read(tmp_path, first + "\n" + second)["x12_segment"]
    r = rows(t)
    assert {x["interchange_control"] for x in r} == {"000000001", "000000002"}
    sts = [
        (x["interchange_control"], x["group_control"], x["transaction_control"])
        for x in r
        if x["segment_id"] == "ST"
    ]
    assert sts == [
        ("000000001", "7", "0001"),
        ("000000001", "7", "0002"),
        ("000000002", "9", "0001"),
    ]
    second_isa = [x for x in r if x["segment_id"] == "ISA"][1]
    assert second_isa["position"] == 1  # positions restart in every interchange


def test_each_interchange_uses_its_own_delimiters(tmp_path):
    other = envelope(BODY, ic="000000002").replace("*", "|").replace("~", "!").replace(":", ">")
    t = read(tmp_path, envelope(BODY) + other)["x12_segment"]
    svc = [x for x in rows(t) if x["segment_id"] == "SVC"]
    assert svc[0]["elements"][0] == "HC:99213" and svc[3]["elements"][0] == "HC>99213"
    assert t.num_rows == 2 * (len(BODY) + 6)


def test_non_835_sets_have_no_loop_ids_and_no_loop_rows(tmp_path):
    text = envelope(
        ["BHT*0019*00*1*20240101*1200*CH~", "NM1*41*2*SUBMITTER~"],
        gs="GS*HC*A*B*20240101*1200*{gc}*X*005010X222A1~",
    ).replace("ST*835", "ST*837")
    tables = read(tmp_path, text, loops="tables")
    assert set(tables["x12_segment"].column("loop_id").to_pylist()) == {None}
    assert all(tables[f"loop_{k}"].num_rows == 0 for k in ("header", "2100", "2110"))


# -- 835 loops ---------------------------------------------------------------------------------


def test_loop_ids_follow_the_835_structure(tmp_path):
    t = read(tmp_path, envelope(BODY))["x12_segment"]
    by = [(x["segment_id"], x["loop_id"]) for x in rows(t) if x["transaction_control"]]
    assert by[0] == ("ST", "header")
    assert ("BPR", "header") in by and ("TRN", "header") in by
    assert ("N1", "1000A") in by and ("N3", "1000A") in by
    assert ("LX", "2000") in by
    assert by.count(("CLP", "2100")) == 2
    assert ("NM1", "2100") in by and by.count(("CAS", "2100")) == 1
    assert ("DTM", "2110") in by and by.count(("CAS", "2110")) == 2
    assert by[-2:] == [("PLB", "summary"), ("SE", "summary")]


def test_loop_tables_columns_and_keys(tmp_path):
    tables = read(tmp_path, envelope(BODY), loops="tables")
    assert {k for k in tables if k.startswith("loop_")} == {
        "loop_header",
        "loop_1000a",
        "loop_1000b",
        "loop_2000",
        "loop_2100",
        "loop_2110",
    }
    clp = tables["loop_2100"]
    assert clp.column("CLP01").to_pylist() == ["C1", "C2"]
    assert clp.column("CLP03").to_pylist() == ["100", "50"]
    assert clp.column("CLP05").to_pylist() == [None, None]  # empty element is null
    assert clp.column("CAS01").to_pylist() == ["PR", None]
    assert clp.column("CAS02").to_pylist() == ["1", None]
    assert clp.column("_parent_id").to_pylist() == [1, 1]
    svc = tables["loop_2110"]
    assert svc.column("SVC01_1").to_pylist() == ["HC", "HC", "HC"]
    assert svc.column("SVC01_2").to_pylist() == ["99213", "99214", "99215"]
    assert svc.column("SVC01_3").to_pylist() == [None, "25", None]
    assert "SVC01" not in svc.column_names
    assert svc.column("CAS02").to_pylist() == ["45", None, None]
    assert svc.column("CAS02__2").to_pylist() == ["2", None, None]  # the second CAS of the line
    assert svc.column("_parent_id").to_pylist() == [1, 2, 2]
    assert tables["loop_header"].column("BPR01").to_pylist() == ["I"]
    assert tables["loop_header"].column("ST02").to_pylist() == ["0001"]
    assert tables["loop_1000a"].column("N102").to_pylist() == ["PAYER"]
    assert tables["loop_1000b"].column("N104").to_pylist() == ["1999999999"]
    assert tables["loop_2000"].column("LX01").to_pylist() == ["1"]
    assert tables["loop_2100"].column("transaction_control").to_pylist() == ["0001", "0001"]


def test_loop_relationships_and_foreign_keys_hold(tmp_path):
    p = tmp_path / "r.x12"
    p.write_text(envelope(BODY, BODY, ic="000000005"))
    src = X12Source()
    tables = src.read_tables(str(p), loops="tables")
    rels = src.read_relationships(str(p), loops="tables")
    assert {(r.parent, r.child) for r in rels} == {
        ("loop_header", "loop_1000a"),
        ("loop_header", "loop_1000b"),
        ("loop_header", "loop_2000"),
        ("loop_2000", "loop_2100"),
        ("loop_2100", "loop_2110"),
    }
    for r in rels:
        parents = set(tables[r.parent].column("_id").to_pylist())
        children = set(tables[r.child].column("_parent_id").to_pylist())
        assert children <= parents and None not in children, (r.parent, r.child)
    # two sets: ids run through the file, children point at their own set's parents
    assert tables["loop_2100"].column("_id").to_pylist() == [1, 2, 3, 4]
    assert tables["loop_2100"].column("_parent_id").to_pylist() == [1, 1, 2, 2]


def test_a_claim_without_lx_gets_an_implicit_loop_2000(tmp_path):
    body = ["BPR*I*1*C*NON~", "CLP*C9*1*10*10**12*P*11*1~"]
    tables = read(tmp_path, envelope(body), loops="tables")
    assert tables["loop_2000"].num_rows == 1 and "LX01" not in tables["loop_2000"].column_names
    assert tables["loop_2100"].column("_parent_id").to_pylist() == [1]


def test_835_tables_from_the_sinks_output(tmp_path):
    (path,) = run(X12Remittance835Sink, sample_tables(), tmp_path)
    tables = X12Source().read_tables(str(path), loops="tables")
    segs = tables["x12_segment"].column("segment_id").to_pylist()
    assert tables["loop_2100"].num_rows == segs.count("CLP")
    assert tables["loop_2110"].num_rows == segs.count("SVC")
    assert tables["loop_2110"].num_rows > 0


def test_loop_tables_need_a_valid_option(tmp_path):
    with pytest.raises(ValueError, match="loops must be"):
        read(tmp_path, envelope(BODY), loops="all")
    with pytest.raises(TypeError, match="unexpected option"):
        read(tmp_path, envelope(BODY), loop="tables")


# -- envelope rules and hostile input ----------------------------------------------------------


def broken(text: str, old: str, new: str) -> str:
    assert old in text
    return text.replace(old, new, 1)


@pytest.mark.parametrize(
    ("old", "new", "match"),
    [
        (
            "IEA*1*000000001",
            "IEA*1*000000002",
            r"IEA at position \d+: IEA02 '000000002' does not match ISA13",
        ),
        ("IEA*1*", "IEA*3*", r"IEA at position \d+: IEA01 says '3' groups but there are 1"),
        ("GE*1*1", "GE*1*2", r"GE at position \d+: GE02 '2' does not match GS06 '1'"),
        ("GE*1*1", "GE*4*1", r"GE at position \d+: GE01 says '4' sets but the group has 1"),
        ("SE*19*0001", "SE*19*0002", r"SE at position \d+: SE02 '0002' does not match ST02"),
        ("SE*19*", "SE*18*", r"SE at position 21: SE01 says '18' segments but the set has 19"),
    ],
)
def test_broken_envelope_rules_name_segment_and_position(tmp_path, old, new, match):
    text = broken(envelope(BODY), old, new)
    with pytest.raises(ValueError, match=match):
        read(tmp_path, text)


def test_non_strict_reads_the_file_and_reports_problems(tmp_path):
    text = broken(envelope(BODY), "SE*19*", "SE*18*")
    text = broken(text, "IEA*1*000000001", "IEA*1*000000009")
    tables = read(tmp_path, text, strict=False)
    problems = rows(tables["x12_problems"])
    assert [(p["segment_id"], p["position"]) for p in problems] == [("SE", 21), ("IEA", 23)]
    assert problems[0]["interchange_control"] == "000000001"
    assert "SE01" in problems[0]["message"] and "IEA02" in problems[1]["message"]
    assert tables["x12_segment"].num_rows == len(BODY) + 6  # nothing was dropped


def test_a_valid_file_has_an_empty_problem_table(tmp_path):
    t = read(tmp_path, envelope(BODY))["x12_problems"]
    assert t.num_rows == 0 and t.column_names == [
        "interchange_control",
        "segment_id",
        "position",
        "message",
    ]


def test_missing_trailers(tmp_path):
    no_iea = envelope(BODY).replace("IEA*1*000000001~", "")
    with pytest.raises(ValueError, match=r"ISA at position 1: interchange has no IEA"):
        read(tmp_path, no_iea)
    no_se = envelope(BODY).replace("SE*19*0001~", "")
    with pytest.raises(ValueError, match=r"transaction set has no SE"):
        read(tmp_path, no_se)
    problems = read(tmp_path, no_se, strict=False)["x12_problems"]
    assert {p["segment_id"] for p in rows(problems)} >= {"ST"}


def test_unterminated_last_segment(tmp_path):
    with pytest.raises(ValueError, match="segment is not terminated"):
        read(tmp_path, envelope(BODY).rstrip("~"))


@pytest.mark.parametrize("cut", [3, 50, 105])
def test_truncated_isa_is_refused_even_when_not_strict(tmp_path, cut):
    isa = ISA.format(ic="000000001")
    assert len(isa) == 106
    for strict in (True, False):
        with pytest.raises(ValueError, match="ISA segment is truncated"):
            read(tmp_path, isa[:cut], strict=strict)


def test_truncated_isa_names_the_count(tmp_path):
    isa = ISA.format(ic="000000001")
    with pytest.raises(ValueError, match=r"106 characters and only 100 remain"):
        read(tmp_path, isa[:100], strict=False)


def test_not_x12_and_bad_isa(tmp_path):
    with pytest.raises(ValueError, match="expected an ISA segment"):
        read(tmp_path, "GS*HP*A*B~")
    bad = ISA.format(ic="000000001").replace("*00501", "|00501")
    with pytest.raises(ValueError, match="16 elements"):
        read(tmp_path, bad + "IEA*0*000000001~")
    clash = ISA.format(ic="000000001")[:-2] + "*~"
    with pytest.raises(ValueError, match="unusable delimiters"):
        read(tmp_path, clash)
    assert read(tmp_path, "   \n")["x12_segment"].num_rows == 0  # an empty file has no segments


def test_a_file_over_the_size_limit_is_refused(tmp_path):
    with pytest.raises(ValueError, match="over the 10-byte limit"):
        read(tmp_path, envelope(BODY), max_bytes=10)


# -- the source --------------------------------------------------------------------------------


def test_can_open_scheme_and_suffixes():
    s = X12Source()
    assert s.can_open("x12://remits/a.txt") and s.can_open("remits/a.x12")
    assert s.can_open("file:///x/A.EDI") and not s.can_open("a.csv")
    assert not s.can_open("abfss://c@a/x.x12") and not s.can_open("hl7v2://a.x12")


def test_scheme_uri_reads_the_file(tmp_path):
    p = tmp_path / "data.txt"
    p.write_text(envelope(BODY))
    t = pa.Table.from_batches(list(X12Source().read(f"x12://{p}")))
    assert t.num_rows == len(BODY) + 6
    assert list(X12Source().read(f"x12://{p}", table="x12_problems")) == []
    assert X12Source().schema(f"x12://{p}", table="x12_problems").names[0] == "interchange_control"
    with pytest.raises(ValueError, match="no table 'nope'"):
        X12Source().schema(f"x12://{p}", table="nope")
    with pytest.raises(ValueError, match="not a x12 file"):
        X12Source().schema(str(tmp_path / "other.csv"))


def test_conformance_kit_and_registration(tmp_path):
    p = tmp_path / "k.x12"
    p.write_text(envelope(BODY))
    kit.check_source(X12Source(), str(p))
    from shape.plugins.host import default_host

    assert type(default_host().get("shape.sources", "x12")).__name__ == "X12Source"


def test_profile_runs_on_every_table(tmp_path):
    p = tmp_path / "p.x12"
    p.write_text(envelope(BODY))
    for name, table in X12Source().read_tables(str(p), loops="tables").items():
        if not table.num_rows:
            continue
        flat = pa.table(
            {
                k: v
                for k, v in zip(table.column_names, table.columns, strict=True)
                if not pa.types.is_list(v.type)
            }
        )
        (entry,) = shape.profile(flat).tables.values()
        assert entry["row_count"] == table.num_rows, name


def test_parse_x12_is_deterministic_and_positions_are_unique_per_interchange():
    a = parse_x12(envelope(BODY))
    b = parse_x12(envelope(BODY))
    assert [(s.position, s.segment_id) for s in a.segments] == [
        (s.position, s.segment_id) for s in b.segments
    ]
    assert len({s.position for s in a.segments}) == len(a.segments)
