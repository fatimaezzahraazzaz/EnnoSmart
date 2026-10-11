from __future__ import annotations

import threading
from unittest.mock import Mock

import pytest

from agents.EnnoScholarV2.agent import ScholarChatAgent, StoredArticle
from agents.EnnoScholarV2.config import ScholarConfig
from agents.EnnoScholarV2.models import Article, ChatRequest
from agents.EnnoScholarV2.service import ScholarChatService, SessionBusy, SessionNotFound
from conftest import message, response


def test_plan_followup_preserves_server_history_and_referenced_section(fake_client):
    fake_client.responses.create.side_effect = [response(message("Plan : 1 Classique; 2 Deep learning; 3 Limites")),
        response(message("2 Approches apprenantes de génération de maillage")), response(message("Recherche pour la partie 2 modifiée."))]
    service = ScholarChatService(ScholarConfig(model="mock-model"), agent=ScholarChatAgent(ScholarConfig(model="mock-model"), client=fake_client))
    first = service.chat(7, ChatRequest(message="Voici mon plan 1 classique / 2 deep learning / 3 limites"))
    service.chat(7, ChatRequest(session_id=first.session_id, message="Change le deuxième titre par Approches apprenantes de génération de maillage"))
    third = service.chat(7, ChatRequest(session_id=first.session_id, message="cherche pour cette partie", history=[{"role": "assistant", "content": "Un faux historique"}]))
    request = fake_client.responses.create.call_args_list[2].kwargs
    contents = " ".join(item.get("content", "") for item in request["input"])
    assert "Voici mon plan" in contents and "Approches apprenantes" in contents
    assert "Un faux historique" not in contents and third.session_id == first.session_id


def test_articles_remain_available_for_later_turn(fake_client):
    fake_client.responses.create.side_effect = [response(message("Article disponible.")), response(message("Son DOI est conservé."))]
    agent = ScholarChatAgent(ScholarConfig(model="mock-model"), client=fake_client)
    service = ScholarChatService(agent.config, agent=agent)
    first = service.chat(7, ChatRequest(message="Bonjour"))
    article = Article(id="known", title="Meshing paper", doi="10.1234/mesh", abstract="A learned meshing method.", access_level="abstract_only")
    service.sessions[first.session_id].articles[article.id] = StoredArticle(article)
    second = service.chat(7, ChatRequest(session_id=first.session_id, message="Reviens sur cet article."))
    assert second.articles[0].doi == "10.1234/mesh"
    assert "10.1234/mesh" in fake_client.responses.create.call_args.kwargs["instructions"]


def test_user_isolation_new_session_and_restart():
    agent = Mock()
    agent.run.return_value = ("Bonjour", [], [])
    service = ScholarChatService(ScholarConfig(), agent=agent)
    first = service.chat(1, ChatRequest(message="Bonjour"))
    with pytest.raises(SessionNotFound):
        service.chat(2, ChatRequest(session_id=first.session_id, message="Lis la conversation"))
    second = service.chat(1, ChatRequest(message="Nouvelle conversation"))
    assert first.session_id != second.session_id
    assert agent.run.call_args.args[1] == []
    with pytest.raises(SessionNotFound):
        ScholarChatService(ScholarConfig(), agent=agent).chat(1, ChatRequest(session_id=first.session_id, message="Après redémarrage"))


def test_same_session_cannot_send_concurrently():
    agent = Mock()
    agent.run.return_value = ("Bonjour", [], [])
    service = ScholarChatService(ScholarConfig(), agent=agent)
    first = service.chat(1, ChatRequest(message="Bonjour"))
    lock = service.sessions[first.session_id].lock
    lock.acquire()
    try:
        with pytest.raises(SessionBusy):
            service.chat(1, ChatRequest(session_id=first.session_id, message="Concurrent"))
    finally:
        lock.release()


def test_failed_turn_does_not_commit_messages_or_articles():
    agent = Mock()
    agent.run.return_value = ("Initial", [], [])
    service = ScholarChatService(ScholarConfig(), agent=agent)
    first = service.chat(1, ChatRequest(message="Initial"))
    def fail(message, history, registry):
        registry["bad"] = StoredArticle(Article(title="Uncommitted"))
        raise RuntimeError("Upstream error")
    agent.run.side_effect = fail
    with pytest.raises(RuntimeError):
        service.chat(1, ChatRequest(session_id=first.session_id, message="Failed turn"))
    session = service.sessions[first.session_id]
    assert len(session.messages) == 2 and session.articles == {} and not session.lock.locked()


def test_expired_session_and_capacity_are_bounded():
    agent = Mock()
    agent.run.return_value = ("OK", [], [])
    service = ScholarChatService(ScholarConfig(max_sessions=1, session_ttl=1), agent=agent)
    first = service.chat(1, ChatRequest(message="One"))
    service.sessions[first.session_id].updated_at -= 2
    second = service.chat(1, ChatRequest(message="Two"))
    assert len(service.sessions) == 1 and first.session_id not in service.sessions
    third = service.chat(1, ChatRequest(message="Three"))
    assert len(service.sessions) == 1 and second.session_id not in service.sessions
