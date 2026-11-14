import asyncio

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
