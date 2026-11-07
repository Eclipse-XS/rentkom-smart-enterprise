from unittest.mock import AsyncMock
from datetime import datetime, timedelta, timezone

from core.api.v1.endpoints import rag as rag_endpoint
from core.config import settings
from core.schemas import RAGQueryResponse


def _headers():
    return {"X-Internal-Token": settings.INTERNAL_API_KEY}


def _response(answer="ok"):
    return RAGQueryResponse(
        answer=answer,
        sources=[],
        citations=[],
        confidence_score=0.8,
        latency_seconds=0.01,
        mode="grounded",
    )


def test_rag_endpoint_requires_authenticated_service_boundary(client):
    response = client.post("/api/v1/rag/ask", json={"question": "Які документи потрібні?"})
    assert response.status_code == 401


def test_rag_sessions_are_isolated_and_keys_do_not_expose_client_ids(client, db_session, monkeypatch):
    histories = []

    async def fake_query(_question, history=None):
        histories.append(list(history or []))
        return _response()

    monkeypatch.setattr(rag_endpoint, "query_rag", fake_query)
    first = client.post(
        "/api/v1/rag/ask",
        json={"question": "Перше питання", "session_id": "1001"},
        headers=_headers(),
    )
    second = client.post(
        "/api/v1/rag/ask",
        json={"question": "Інша сесія", "session_id": "2002"},
        headers=_headers(),
    )
    follow_up = client.post(
        "/api/v1/rag/ask",
        json={"question": "Уточнення", "session_id": "1001"},
        headers=_headers(),
    )

    assert first.status_code == second.status_code == follow_up.status_code == 200
    assert histories[0] == []
    assert histories[1] == []
    assert histories[2][0]["content"] == "Перше питання"

    from core.models import SystemConfig
    keys = [row.key for row in db_session.query(SystemConfig).filter(SystemConfig.key.like("rag_history:%")).all()]
    assert all("1001" not in key and "2002" not in key for key in keys)


def test_typing_chat_must_match_telegram_session(client, monkeypatch):
    monkeypatch.setattr(rag_endpoint, "query_rag", AsyncMock(return_value=_response()))
    response = client.post(
        "/api/v1/rag/ask",
        json={"question": "Питання", "session_id": "1001", "typing_chat_id": 2002},
        headers=_headers(),
    )
    assert response.status_code == 400
    assert "must match" in response.json()["detail"]


def test_rag_rejects_oversized_and_malformed_input(client):
    oversized = client.post(
        "/api/v1/rag/ask",
        json={"question": "x" * 1001},
        headers=_headers(),
    )
    malformed_session = client.post(
        "/api/v1/rag/ask",
        json={"question": "Питання", "session_id": "../../other"},
        headers=_headers(),
    )
    assert oversized.status_code == 422
    assert malformed_session.status_code == 422


def test_expired_history_is_not_reused(client, db_session, monkeypatch):
    from core.models import SystemConfig

    session_id = "expired-session"
    key = rag_endpoint._history_key(
        rag_endpoint.AuthenticatedActor(actor_id="internal-service", role="internal"),
        session_id,
    )
    db_session.add(SystemConfig(
        key=key,
        value='[{"role":"user","content":"private old history"}]',
        updated_at=datetime.now(timezone.utc) - timedelta(hours=25),
    ))
    db_session.commit()
    seen_history = []

    async def fake_query(_question, history=None):
        seen_history.extend(history or [])
        return _response()

    monkeypatch.setattr(rag_endpoint, "query_rag", fake_query)
    response = client.post(
        "/api/v1/rag/ask",
        json={"question": "Нове питання", "session_id": session_id},
        headers=_headers(),
    )

    assert response.status_code == 200
    assert seen_history == []
    db_session.expire_all()
    refreshed = db_session.get(SystemConfig, key)
    assert refreshed is None or "private old history" not in refreshed.value
