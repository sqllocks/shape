from shape.admin import Quotas
from shape.ai import contract_proposal
from shape.enterprise import AuditChain, Authorizer, KeyRing, Principal
from shape.execution import ExecutionPlan
from shape.federation import FederatedNode, aggregate
from shape.governance import ApprovalGate, attest
from shape.graph import generate, profile
from shape.marketplace import Catalog
from shape.privacy.advanced import k_anonymous, laplace, reidentification_risk
from shape.reproducibility import ReproManifest, digest


def test_federation():
    a = FederatedNode("a", {"rows": 10, "columns": {"x": {"count": 10, "mean": 2}}})
    b = FederatedNode("b", {"rows": 20, "columns": {"x": {"count": 20, "mean": 5}}})
    x = aggregate([a, b])
    assert x["rows"] == 30 and x["columns"]["x"]["mean"] == 4


def test_enterprise():
    p = Principal("u", "t", ("writer",))
    assert Authorizer().allowed(p, "write")
    k = KeyRing()
    k.rotate("1", b"x")
    assert k.get() == b"x"
    a = AuditChain()
    a.append({"x": 1})
    assert a.verify()


def test_privacy():
    assert (
        len(k_anonymous({"a": 1, "b": 5}, 2)) == 1
        and 0 <= reidentification_risk({"a": 1, "b": 2}) <= 1
        and isinstance(laplace(1, 1, 1), float)
    )


def test_execution():
    assert sorted(ExecutionPlan(2).map(sum, [[1], [2]])) == [1, 2]


def test_graph():
    assert profile([(1, 2), (1, 3)])["edges"] == 2 and len(generate(10, 2)) == 20


def test_governance():
    g = ApprovalGate(2)
    g.approve("a")
    g.approve("b")
    assert g.passed and attest("x", {})["id"]


def test_repro():
    assert len(digest(ReproManifest("a", "1", 1))) == 64


def test_ai():
    assert (
        contract_proposal({"columns": {"x": {"kind": "numeric", "null_count": 0}}})["status"]
        == "proposal"
    )


def test_admin():
    q = Quotas({"x": 1})
    assert q.consume("x") == 1


def test_marketplace():
    c = Catalog()
    c.publish("official", "person", "1", "abc")
    assert c.search("person")
    c.revoke("official", "person", "1")
    assert not c.search("person")
