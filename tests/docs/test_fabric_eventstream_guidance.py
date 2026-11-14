"""Offline checks for the Eventstream deployment guide; no tenant access."""

import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HEADING = "### 1.2 Eventstream deployment and Eventhouse ingestion"
LEARN = "https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/"


def guide() -> str:
    text = (ROOT / "integrations/fabric/RUNBOOK.md").read_text(encoding="utf-8")
    assert HEADING in text
    return text.split(HEADING, 1)[1].split("\n## ", 1)[0]


def test_activation_precedes_sending_in_numbered_guide() -> None:
    text = guide()
    steps = re.findall(r"^\d+\. (.*?)(?=^\d+\. |\Z)", text, re.M | re.S)
    activate = next(i for i, step in enumerate(steps) if "**Activate ingestion**" in step)
    send = next(i for i, step in enumerate(steps) if step.startswith("Send events"))
    assert activate < send
    for required in (
        "Custom endpoint",
        "Event Hub",
        "SHAPE_EVENTSTREAM_CONNECTION_STRING",
        "Data preview",
        "Data insights",
        "Query table",
        "EventstreamEmitter",
        "EventhouseEmitter",
        "EventhouseWriter",
        "write_table",
        "row_count",
    ):
        assert required in text


def test_early_event_loss_warning_and_recovery() -> None:
    text = guide()
    assert "**Early-event loss warning:**" in text
    assert "before activation" in text
    assert "missing from the destination" in text
    assert "routing is ready" in text
    assert "Custom time" in text


def test_spark_notebook_destination_requires_manual_setup_after_git() -> None:
    text = guide()
    assert "Spark Notebook" in text
    assert "not supported by Git integration or deployment pipelines" in text
    assert "manually add and configure" in text
    assert "target workspace" in text
    assert "Direct Ingestion" in text and "reconfigure the connection" in text


def test_learn_sources_and_retrieval_date_are_recorded() -> None:
    text = guide()
    for page in (
        "add-source-custom-app",
        "eventstream-cicd",
        "add-destination-kql-database",
        "stream-real-time-events-from-custom-app-to-kusto",
    ):
        assert LEARN + page in text
    retrieved = re.search(r"retrieved (\d{4}-\d{2}-\d{2})", text)
    assert retrieved is not None
    assert date.fromisoformat(retrieved[1]) == date(2026, 11, 14)
