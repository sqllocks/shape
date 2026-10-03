"""W5-07: the HL7 v2 reader (segments, per-segment tables, MLLP, escapes, hostile input)."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest
from shape_healthcare_standards.hl7v2.read import parse_hl7
from shape_healthcare_standards.sources import Hl7v2Source

import shape
from shape.plugins import kit

MSG1 = "\r".join(
    [
        "MSH|^~\\&|SENDAPP|SENDFAC|RECVAPP|RECVFAC|20240101120000||ADT^A01^ADT_A01|MSG0001|P|2.5",
        "EVN|A01|20240101120000",
        "PID|1||1001^^^HOSP^MR~1002^^^OTHER^PI||DOE^JANE^Q||19800101|F",
        "PV1|1|I|WARD^101^A",
        "OBX|1|ST|NOTE||first",
        "OBX|2|ST|NOTE||second",
    ]
)
MSG2 = "\r".join(
    [
        "MSH|^~\\&|SENDAPP|SENDFAC|RECVAPP|RECVFAC|20240102||ORU^R01|MSG0002|P|2.5",
        "PID|1||2001||ROE^RICK",
    ]
)


def read(tmp_path: Path, text: str, name: str = "m.hl7", **options):
    p = tmp_path / name
    p.write_bytes(text.encode("utf-8"))
    return Hl7v2Source().read_tables(str(p), **options)


def rows(t: pa.Table) -> list[dict]:
    return t.to_pylist()


def test_segment_table_layout(tmp_path):
    t = read(tmp_path, MSG1)["hl7_segment"]
    assert t.column_names == [
        "message_index",
        "message_control_id",
        "message_type",
        "position",
        "segment_id",
        "fields",
    ]
    r = rows(t)
    assert [x["segment_id"] for x in r] == ["MSH", "EVN", "PID", "PV1", "OBX", "OBX"]
    assert [x["position"] for x in r] == [1, 2, 3, 4, 5, 6]
    assert {x["message_control_id"] for x in r} == {"MSG0001"}
    assert {x["message_type"] for x in r} == {"ADT^A01^ADT_A01"}
    assert {x["message_index"] for x in r} == {1}
    assert t.schema.field("fields").type == pa.list_(pa.list_(pa.list_(pa.string())))


def test_fields_keep_repetitions_and_components_as_nested_lists(tmp_path):
    r = rows(read(tmp_path, MSG1)["hl7_segment"])
    pid = r[2]["fields"]
    assert pid[2] == [["1001", "", "", "HOSP", "MR"], ["1002", "", "", "OTHER", "PI"]]  # PID-3
    assert pid[4] == [["DOE", "JANE", "Q"]]  # PID-5
    assert pid[1] == [[""]]  # PID-2 is empty but keeps its place
    msh = r[0]["fields"]
    assert msh[0] == [["|"]] and msh[1] == [["^~\\&"]]  # MSH-1 and MSH-2 are the separators
    assert (
        msh[2] == [["SENDAPP"]]
        and msh[9] == [["MSG0001"]]
        and msh[8][0] == ["ADT", "A01", "ADT_A01"]
    )


def test_several_messages_in_one_file_and_line_endings(tmp_path):
    for sep in ("\r", "\n", "\r\n"):
        text = (MSG1 + "\r" + MSG2).replace("\r", sep)
        t = read(tmp_path, text)["hl7_segment"]
        assert sorted(set(t.column("message_index").to_pylist())) == [1, 2]
        assert t.column("message_control_id").to_pylist()[-1] == "MSG0002"
        assert t.column("position").to_pylist()[-2:] == [1, 2]  # positions restart per message


def test_mllp_framing_is_stripped(tmp_path):
    framed = "\x0b" + MSG1 + "\r\x1c\r" + "\x0b" + MSG2 + "\r\x1c\r"
    t = read(tmp_path, framed)["hl7_segment"]
    assert set(t.column("message_control_id").to_pylist()) == {"MSG0001", "MSG0002"}
    assert t.column("segment_id").to_pylist()[0] == "MSH"


def test_escape_sequences_are_decoded_after_splitting(tmp_path):
    text = "\r".join(
        [
            "MSH|^~\\&|A|B|C|D|20240101||ADT^A01|M1|P|2.5",
            r"NTE|1||a\F\b\S\c\T\d\R\e\E\f\.br\g\X41\\X0D0A\\H\bold\N\ "
            r"\Zlocal\ \Xzz\ \unterminated",
        ]
    )
    nte = rows(read(tmp_path, text)["hl7_segment"])[1]["fields"]
    assert nte[2] == [
        ["a|b^c&d~e\\f\ng" + "A" + "\r\n" + "bold" + " \\Zlocal\\ \\Xzz\\ \\unterminated"]
    ]


def test_other_encoding_characters_come_from_msh2(tmp_path):
    text = "MSH#*+\\!#A#B#C#D#20240101##ADT*A01#M7#P#2.5\rPID#1##X*Y+Z*W"
    r = rows(read(tmp_path, text)["hl7_segment"])
    assert r[0]["message_control_id"] == "M7" and r[0]["message_type"] == "ADT*A01"
    # component '*', repetition '+': "X*Y+Z*W" is two repetitions of two components
    assert r[1]["fields"][2] == [["X", "Y"], ["Z", "W"]]


def test_segment_tables(tmp_path):
    tables = read(tmp_path, MSG1 + "\r" + MSG2, segments="tables")
    assert set(tables) == {
        "hl7_segment",
        "hl7_message",
        "hl7_msh",
        "hl7_evn",
        "hl7_pid",
        "hl7_pv1",
        "hl7_obx",
    }
    assert rows(tables["hl7_message"]) == [
        {"message_index": 1, "message_control_id": "MSG0001", "message_type": "ADT^A01^ADT_A01"},
        {"message_index": 2, "message_control_id": "MSG0002", "message_type": "ORU^R01"},
    ]
    pid = tables["hl7_pid"]
    assert pid.column("message_index").to_pylist() == [1, 2]
    assert pid.column("PID_3_1").to_pylist() == ["1001", "2001"]
    assert pid.column("PID_3_4").to_pylist() == ["HOSP", None]
    assert pid.column("PID_3_1__2").to_pylist() == ["1002", None]  # second repetition
    assert pid.column("PID_5_1").to_pylist() == ["DOE", "ROE"]
    assert pid.column("PID_5_2").to_pylist() == ["JANE", "RICK"]
    assert pid.column("PID_5_3").to_pylist() == ["Q", None]
    assert pid.column("PID_8").to_pylist() == ["F", None]
    assert pid.column("PID_2").to_pylist() == [None, None]  # empty is null here
    assert "PID_3" not in pid.column_names and "PID_5" not in pid.column_names
    obx = tables["hl7_obx"]
    assert obx.column("position").to_pylist() == [5, 6]
    assert obx.column("OBX_5").to_pylist() == ["first", "second"]
    assert tables["hl7_msh"].column("MSH_1").to_pylist() == ["|", "|"]
    assert tables["hl7_msh"].column("MSH_9_2").to_pylist() == ["A01", "R01"]


def test_segment_table_relationships_hold(tmp_path):
    p = tmp_path / "r.hl7"
    p.write_text(MSG1 + "\r" + MSG2)
    src = Hl7v2Source()
    tables = src.read_tables(str(p), segments="tables")
    rels = src.read_relationships(str(p), segments="tables")
    assert {r.child for r in rels} == {"hl7_msh", "hl7_evn", "hl7_pid", "hl7_pv1", "hl7_obx"}
    for r in rels:
        assert r.parent == "hl7_message"
        parents = set(tables[r.parent].column(r.parent_columns[0]).to_pylist())
        assert set(tables[r.child].column(r.child_columns[0]).to_pylist()) <= parents
    assert src.read_relationships(str(p)) == []


def test_missing_msh_control_id_and_type_are_null(tmp_path):
    t = read(tmp_path, "MSH|^~\\&|A\rPID|1")["hl7_segment"]
    assert t.column("message_control_id").to_pylist() == [None, None]
    assert t.column("message_type").to_pylist() == [None, None]


# -- hostile and malformed input ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "PID|1||123\rPV1|1",
        "EVN|A01\rMSH|^~\\&|A",
        "\x0bPID|1\r\x1c\r",
        "garbage without any segments",
    ],
)
def test_a_message_without_msh_first_is_refused(tmp_path, text):
    with pytest.raises(ValueError, match="no MSH segment"):
        read(tmp_path, text)


@pytest.mark.parametrize("text", ["", "\r\n\r\n", "\x0b\x1c\r"])
def test_empty_input_is_refused(tmp_path, text):
    with pytest.raises(ValueError, match="no MSH segment"):
        read(tmp_path, text)


def test_bad_encoding_characters_and_segment_ids(tmp_path):
    with pytest.raises(ValueError, match="MSH-2"):
        read(tmp_path, "MSH|^~|A")
    with pytest.raises(ValueError, match="MSH-2"):
        read(tmp_path, "MSH|")
    with pytest.raises(ValueError, match=r"message 1, segment 2: 'pid' is not a segment id"):
        read(tmp_path, "MSH|^~\\&|A\rpid|1")


def test_option_and_size_validation(tmp_path):
    with pytest.raises(ValueError, match="segments must be"):
        read(tmp_path, MSG1, segments="all")
    with pytest.raises(TypeError, match="unexpected option"):
        read(tmp_path, MSG1, loops="tables")
    with pytest.raises(ValueError, match="over the 10-byte limit"):
        read(tmp_path, MSG1, max_bytes=10)


def test_latin1_files_are_read(tmp_path):
    p = tmp_path / "l.hl7"
    p.write_bytes("MSH|^~\\&|A\rPID|1||x||M\xfcller".encode("latin-1"))
    t = Hl7v2Source().read_tables(str(p))["hl7_segment"]
    assert t.to_pylist()[1]["fields"][4] == [["Müller"]]


# -- the source --------------------------------------------------------------------------------


def test_can_open():
    s = Hl7v2Source()
    assert s.can_open("a.hl7") and s.can_open("file:///x/A.HL7") and s.can_open("hl7v2://a.dat")
    assert not s.can_open("a.x12") and not s.can_open("abfss://c@a/x.hl7")
    assert not s.can_open("x12://a.hl7")


def test_read_and_schema_select_a_table(tmp_path):
    p = tmp_path / "m.hl7"
    p.write_text(MSG1)
    s = Hl7v2Source()
    assert pa.Table.from_batches(list(s.read(str(p)))).num_rows == 6
    pid = pa.Table.from_batches(list(s.read(str(p), segments="tables", table="hl7_pid")))
    assert pid.num_rows == 1
    assert s.schema(str(p), segments="tables", table="hl7_pid").names[0] == "message_index"
    with pytest.raises(ValueError, match="no table 'hl7_zzz'"):
        s.schema(str(p), table="hl7_zzz")


def test_conformance_kit_and_registration(tmp_path):
    p = tmp_path / "k.hl7"
    p.write_text(MSG1)
    kit.check_source(Hl7v2Source(), str(p))
    from shape.plugins.host import default_host

    assert type(default_host().get("shape.sources", "hl7v2")).__name__ == "Hl7v2Source"


def test_profile_runs_on_every_table(tmp_path):
    for name, table in read(tmp_path, MSG1 + "\r" + MSG2, segments="tables").items():
        flat = pa.table(
            {
                c: table.column(c)
                for c in table.column_names
                if not pa.types.is_list(table.schema.field(c).type)
            }
        )
        (entry,) = shape.profile(flat).tables.values()
        assert entry["row_count"] == table.num_rows, name


def test_parse_is_deterministic():
    assert parse_hl7(MSG1 + "\r" + MSG2) == parse_hl7(MSG1 + "\r" + MSG2)
