"""``shape.privacy.assess_summary``: rare retained categories and near-unique fields (AUD-tests)."""

from shape.privacy import LeakageFinding, LeakageReport, assess_summary


def test_an_empty_summary_has_no_findings_and_is_releasable():
    assert assess_summary({}) == LeakageReport(())
    assert assess_summary({}).releasable
    assert assess_summary({"topk": None, "count": None}).findings == ()


def test_every_rare_retained_category_is_a_high_finding():
    report = assess_summary({"topk": [["a", 3], ["b", 100], ["c", 5], ["d", 6]]})
    assert [f.kind for f in report.findings] == ["rare_value", "rare_value"]
    assert all(f.severity == "high" for f in report.findings)
    assert not report.releasable


def test_the_rare_threshold_is_configurable_and_short_items_are_ignored():
    summary = {"topk": [["a", 3], ["b"]]}
    assert assess_summary(summary, rare_threshold=2).findings == ()
    assert len(assess_summary(summary, rare_threshold=3).findings) == 1


def test_a_near_unique_field_is_a_high_finding():
    report = assess_summary({"count": 100, "distinct_estimate": 95})
    assert report.findings == (
        LeakageFinding("near_unique", "high", "field appears near-unique and may be identifying"),
    )
    assert assess_summary({"count": 100, "distinct_estimate": 94}).findings == ()
    assert assess_summary({"count": 0, "distinct_estimate": 0}).findings == ()


def test_only_high_and_critical_findings_block_release():
    low = LeakageReport((LeakageFinding("x", "low", ""), LeakageFinding("y", "medium", "")))
    assert low.releasable
    assert not LeakageReport((LeakageFinding("x", "critical", ""),)).releasable
