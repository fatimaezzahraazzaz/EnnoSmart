from ennodiagnostic_v2.frascati import FrascatiEvaluator
from ennodiagnostic_v2.schemas import CanonicalEntity

def test_no_fake_zero_when_not_evaluated():
    lock = CanonicalEntity(entity_id="l1", kind="lock", statement="Verrou")
    result = FrascatiEvaluator(None).assess([lock])
    assert result[0].status == "not_evaluated"
