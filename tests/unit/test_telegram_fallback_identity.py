import asyncio
import hashlib
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock

import pytest

from core.models import TelegramInbox
from core.services.telegram_idempotency import CachedResponse, TelegramIdempotencyManager


def payload(chat_id, message_id=None, text="hello", update_id=None):
    message = {"chat": {"id": chat_id}, "text": text}
    if message_id is not None:
        message["message_id"] = message_id
    result = {"message": message}
    if update_id is not None:
        result["update_id"] = update_id
    return result


def test_fallback_pairs_are_distinct_and_repeatable():
    manager = TelegramIdempotencyManager()
    updates = [payload(1, 23), payload(12, 3), payload(-1, 23), payload(-12, 3)]
    ids = [manager.parse_update_id(update)[0] for update in updates]
    assert len(set(ids)) == len(updates)
    assert all(-(2**60) <= value < 0 for value in ids)
    assert ids == [manager.parse_update_id(update)[0] for update in updates]


def test_real_update_id_is_preserved():
    assert TelegramIdempotencyManager().parse_update_id(payload(-12, 3, update_id=123))[0] == 123


def test_message_identity_ignores_text_but_text_fallback_distinguishes_content():
    manager = TelegramIdempotencyManager()
    assert manager.parse_update_id(payload(1, 23, "first"))[0] == manager.parse_update_id(payload(1, 23, "edited"))[0]
    ids = [manager.parse_update_id(update)[0] for update in [payload(1), payload(1, text="other"), payload(2)]]
    assert len(set(ids)) == 3
    assert manager.parse_update_id(payload(1))[0] == manager.parse_update_id(payload(1, text=" hello "))[0]
    assert manager.get_event_key(payload(1, 23, "first")) == manager.get_event_key(payload(1, 23, "edited"))


def test_text_only_fallback_preserves_legacy_id():
    digest = hashlib.sha256(b"1:hello").hexdigest()[:14]
    assert TelegramIdempotencyManager().parse_update_id(payload(1))[0] == int(digest, 16) % (2**60)


def test_previously_colliding_messages_process_independently(db_session):
    async def check():
        manager = TelegramIdempotencyManager()
        first, second = payload(1, 23), payload(12, 3)
        for update in (first, second):
            key = manager.get_event_key(update)
            acquired, cached = await manager.acquire_or_get_cached(key, db=db_session, update_data=update)
            assert acquired and cached is None
            await manager.finish_processing(
                key, CachedResponse(route="test", response_text="done"),
                db=db_session, update_data=update,
            )
        acquired, cached = await manager.acquire_or_get_cached(
            manager.get_event_key(first), db=db_session, update_data=first,
        )
        assert not acquired and cached.is_duplicate

    asyncio.run(check())


@pytest.mark.parametrize("status,stored_text", [
    ("COMPLETED", "hello"), ("COMPLETED", "before edit"),
    ("PROCESSING", "hello"), ("FAILED", "hello"),
])
def test_legacy_delivery_is_not_replayed_or_moved(db_session, status, stored_text):
    row = TelegramInbox(
        bot_id="rentkom_bot", update_id=123, chat_id=1, message_text=stored_text,
        status=status, locked_at=datetime.now(timezone.utc) - timedelta(hours=1),
        response_payload=CachedResponse(route="original", response_text="already sent").model_dump()
        if status == "COMPLETED" else None,
    )
    db_session.add(row)
    db_session.commit()
    manager = TelegramIdempotencyManager()
    update = payload(1, 23)
    acquired, cached = asyncio.run(manager.acquire_or_get_cached(
        manager.get_event_key(update), db=db_session, update_data=update,
    ))
    assert not acquired and cached.is_duplicate
    expected_route = "original" if status == "COMPLETED" and stored_text == "hello" else "Legacy Identity Review"
    assert cached.route == expected_route
    assert db_session.query(TelegramInbox).count() == 1
    assert row.update_id == 123 and row.status == status


def test_legacy_collision_in_another_chat_does_not_suppress_new_message(db_session):
    db_session.add(TelegramInbox(
        bot_id="rentkom_bot", update_id=123, chat_id=1, message_text="hello",
        status="COMPLETED", response_payload=CachedResponse(route="old", response_text="done").model_dump(),
    ))
    db_session.commit()
    manager = TelegramIdempotencyManager()
    update = payload(12, 3)
    acquired, cached = asyncio.run(manager.acquire_or_get_cached(
        manager.get_event_key(update), db=db_session, update_data=update,
    ))
    assert acquired and cached is None
    assert db_session.query(TelegramInbox).count() == 2


def test_lost_postgresql_claim_never_falls_back_to_memory():
    db = Mock()
    db.get_bind.return_value.dialect.name = "postgresql"
    db.execute.return_value.fetchone.return_value = None
    manager = TelegramIdempotencyManager()
    update = payload(1, 23, update_id=456)
    acquired, cached = asyncio.run(manager.acquire_or_get_cached(
        manager.get_event_key(update), db=db, update_data=update,
    ))
    assert not acquired and cached.is_duplicate
    assert manager._in_progress == {}
    assert db.execute.call_count == 2


def test_unavailable_legacy_guard_fails_closed_even_in_demo():
    db = Mock()
    db.query.side_effect = RuntimeError("database unavailable")
    manager = TelegramIdempotencyManager()
    update = payload(1, 23)
    with pytest.raises(RuntimeError, match="Authoritative database"):
        asyncio.run(manager.acquire_or_get_cached(
            manager.get_event_key(update), db=db, update_data=update,
        ))
    assert manager._in_progress == {}
