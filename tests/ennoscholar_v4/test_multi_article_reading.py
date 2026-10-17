"""Several selected works are read in rotation, guided by each card's missing dimensions."""
import time
from dataclasses import replace
from unittest.mock import Mock

from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.models import Article
from agents.EnnoScholarV4.scientific_reading import reading_tasks, next_task, prepare_reading
from agents.EnnoScholarV4.toolkit import ScholarTools
from tests.ennoscholar_v4.test_progressive_reading import prepare, save


def long_paper(i):
    """Method-heavy prose first: results and limits sit far down the reading order."""
    chunks = [{"section": "Methods", "page": 2+j, "text": f"Method {i}.{j}: the model architecture uses mechanism K{j}, "
        f"equation E{j}, parameter alpha={j} under condition C{j} with an algorithm network."} for j in range(14)]
    chunks += [{"section": "Experiments", "page": 20+j, "text": f"Experiment {i}.{j}: dataset D{j}, protocol P{j}."} for j in range(2)]
    chunks += [{"section": "Results", "page": 30+j, "text": f"Result {i}.{j}: error {j}.5 % on D{j}."} for j in range(2)]
    chunks += [{"section": "Limitations", "page": 40+j, "text": f"Limitation {i}.{j}: fails on condition X{j}."} for j in range(2)]
    return Article(id=f"paper_long_{i}", title=f"Long study {i}", doi=f"10.1234/long-{i}", year=2024,
        authors=[f"Author {i}"], verified=True, selected=True, url=f"https://example.org/long{i}",
        fulltext={"url": f"https://example.org/long{i}", "text": "\n\n".join(c["text"] for c in chunks),
            "chunks": chunks, "truncated": False})


def corpus(count):
    papers = [long_paper(i) for i in range(count)]
    state = {"messages": [{"role": "user", "content": "Compare les méthodes, résultats et limites des articles."}],
        "articles": {p.id: p.durable() for p in papers}}
    sources = Mock()
    sources.verify.side_effect = lambda p, deadline: p.model_copy(update={"verified": True})
    config = replace(ScholarV4Config(api_key="mock", web_search_enabled=False), max_tool_rounds=6)
    return state, ScholarTools(state, sources, config)


def read_windows(state, toolkit, limit):
    order = []
    reading_tasks(state, toolkit.registry)
    while len(order) < limit and (task := next_task(state, toolkit.registry)) is not None:
        packet = prepare(state, toolkit, task)
        order.append((task["article_id"], [p["section"] for p in packet["passages"]]))
        save(state, toolkit, packet)
        reading_tasks(state, toolkit.registry)
    return order


def test_windows_rotate_across_selected_works_instead_of_draining_the_first():
    state, toolkit = corpus(4)
    order = read_windows(state, toolkit, 8)
    counts = {aid: sum(a == aid for a, _ in order) for aid in toolkit.registry}
    assert set(counts.values()) == {2}


def test_first_window_is_balanced_across_core_dimensions():
    # Method-heavy prose comes first in the paper; the first window still
    # brings protocol, numeric results and limits (a work read once is usable).
    state, toolkit = corpus(1)
    order = read_windows(state, toolkit, 2)
    first = order[0][1]
    assert {"Methods", "Experiments", "Results", "Limitations"} <= set(first)
    task = state["scientific_reading"]["tasks"][0]
    assert task["windows_read"] == 2
    order += read_windows(state, toolkit, 2)
    sections = {s for _, window in order for s in window}
    assert {"Methods", "Experiments", "Results", "Limitations"} <= sections


def test_every_work_reaches_core_dimensions_before_any_extra_depth():
    state, toolkit = corpus(3)
    order = read_windows(state, toolkit, 30)
    tasks = {t["article_id"]: t for t in state["scientific_reading"]["tasks"]}
    # Rotation: at every step the window counts of the works differ by at most one.
    for index in range(1, len(order)+1):
        counts = [sum(a == aid for a, _ in order[:index]) for aid in tasks]
        assert max(counts)-min(counts) <= 1
    assert all(t["core_covered"] for t in tasks.values())
    # Each examined passage is shown once: no repeated reading.
    shown = [eid for t in tasks.values() for eid in t["examined_evidence_ids"]]
    assert len(shown) == len(set(shown))


def test_failed_access_is_not_retried_on_a_later_message():
    state, toolkit = corpus(1)
    paper = toolkit.registry["paper_long_0"]
    paper.fulltext = {}
    paper.abstract = "We propose a learned mesher; validation is limited to simple parts."
    toolkit.save()
    calls = []
    def run(name, args):
        calls.append(name)
        if name == "get_fulltext":
            return {"article_id": paper.id, "warning": "unavailable"}
        return toolkit.execute(name, args, time.monotonic()+30)
    task = reading_tasks(state, toolkit.registry)["tasks"][0]
    prepare_reading(state, toolkit, task, run)
    assert calls.count("get_fulltext") == 1
    state.pop("automatic_reading_focus", None)
    task["pending"] = None
    later = ScholarTools(state, toolkit.sources, toolkit.config)
    task = reading_tasks(state, later.registry)["tasks"][0]
    prepare_reading(state, later, task, run)
    assert calls.count("get_fulltext") == 1
