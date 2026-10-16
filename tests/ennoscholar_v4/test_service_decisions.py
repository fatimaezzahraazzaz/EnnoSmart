"""Interface decisions (keep, hold, discard, restore, reorder) through the service layer."""
import copy

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.database import Base
from db.models import User
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.models import Article
from agents.EnnoScholarV4.service import ScholarV4Service
from agents.EnnoScholarV4.session import ScholarV4Session


@pytest.fixture
def service(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "decisions.sqlite"), connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(User(id=1, full_name="Test", email="test@example.org", hashed_password="not-a-password", is_active=True))
        db.commit()
    result = ScholarV4Service(factory, ScholarV4Config(api_key="mock"), object)
    yield result
    result.pool.shutdown(wait=True)
    engine.dispose()


def seeded(service):
    sid = service.create(1)["id"]
    papers = [Article(id=f"paper_{i}0000000", title=f"Study {i}", doi=f"10.1234/s{i}", verified=True) for i in range(3)]
    with service.session_factory() as db:
        row = db.get(ScholarV4Session, sid)
        data = copy.deepcopy(row.data)
        data["articles"] = {p.id: p.durable() for p in papers}
        data["evaluation"] = {"recommended_article_ids": [p.id for p in papers]}
        row.data = data
        db.commit()
    return sid, [p.id for p in papers]


def test_hold_restore_and_order_are_saved_and_exposed_to_the_interface(service):
    sid, ids = seeded(service)
    service.select_article(1, sid, ids[1], None, "hold")
    session = service.read(1, sid)
    assert session["held_article_ids"] == [ids[1]]
    assert session["recommended_article_ids"] == [ids[0], ids[2]]
    assert any(a["id"] == ids[1] for a in session["articles"])
    service.order_articles(1, sid, [ids[2], ids[0]])
    assert service.read(1, sid)["recommended_article_ids"] == [ids[2], ids[0]]
    service.select_article(1, sid, ids[1], None, "accept")
    session = service.read(1, sid)
    assert session["held_article_ids"] == [] and session["recommended_article_ids"][-1] == ids[1]
    assert [d.get("decision") for d in session["consultant_decisions"] if d.get("decision")] == ["hold", "accept"]
    held = service.articles(1, sid, 0, 50, "", [], "on_hold")
    assert held["total"] == 0


def test_rejected_work_cannot_be_ordered_and_old_selected_contract_still_works(service):
    sid, ids = seeded(service)
    service.select_article(1, sid, ids[0], False)
    with pytest.raises(ValueError):
        service.order_articles(1, sid, [ids[0], ids[1]])
    service.select_article(1, sid, ids[0], True)
    restored = next(a for a in service.read(1, sid)["articles"] if a["id"] == ids[0])
    assert restored["selection_override"] is True and restored["selected"]
