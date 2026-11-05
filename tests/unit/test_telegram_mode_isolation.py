import pytest
from unittest.mock import patch, AsyncMock
from core.database import SessionLocal
from core.api.v1.endpoints.telegram import (
    get_telegram_bot_mode,
    set_telegram_bot_mode,
    process_telegram_update,
    TelegramUpdateMock,
)
from core.schemas import RAGQueryResponse
from core.config import settings


@pytest.fixture
def db_session():
    db = SessionLocal()
    original_mode = get_telegram_bot_mode(db)
    yield db
    # Restore original mode after test
    set_telegram_bot_mode(original_mode, db)
    db.close()


def test_telegram_mode_db_crud(db_session):
    """Test getting and setting telegram mode in PostgreSQL public.system_config."""
    m1, _ = set_telegram_bot_mode("LR2", db_session)
    assert m1 == "LR2"
    assert get_telegram_bot_mode(db_session) == "LR2"

    m2, changed = set_telegram_bot_mode("LR6", db_session)
    assert m2 == "LR6"
    assert changed is True
    assert get_telegram_bot_mode(db_session) == "LR6"

    # Reset
    set_telegram_bot_mode("LR2", db_session)
    assert get_telegram_bot_mode(db_session) == "LR2"


@pytest.mark.anyio
async def test_lr2_mode_isolation_and_keyboard(db_session):
    """
    In LR2 mode:
      - /start returns Start Menu WITH persistent reply keyboard
      - Menu buttons return exact business texts
      - Unrecognized queries return LR2 Fallback WITH keyboard and NEVER touch RAG.
    """
    set_telegram_bot_mode("LR2", db_session)

    # 1. Start command
    up1 = TelegramUpdateMock(
        update_id=910001,
        message={"chat": {"id": 11111}, "text": "/start"}
    )
    res1 = await process_telegram_update(up1, db=db_session)
    assert res1.route == "Start Menu"
    assert res1.reply_markup is not None
    assert "keyboard" in res1.reply_markup
    keyboard_texts = [btn["text"] for row in res1.reply_markup["keyboard"] for btn in row]
    assert "🏢 Процес купівлі/продажу" in keyboard_texts
    assert "🔑 Послуги для орендодавців" in keyboard_texts

    # 2. Buying process
    up2 = TelegramUpdateMock(
        update_id=910002,
        message={"chat": {"id": 11111}, "text": "🏢 Процес купівлі/продажу"}
    )
    res2 = await process_telegram_update(up2, db=db_session)
    assert res2.route == "Купівля/Продаж"
    assert "Процес купівлі/продажу" in res2.response_text

    # 3. Landlord services
    up3 = TelegramUpdateMock(
        update_id=910003,
        message={"chat": {"id": 11111}, "text": "🔑 Послуги для орендодавців"}
    )
    res3 = await process_telegram_update(up3, db=db_session)
    assert res3.route == "Орендодавцям"
    assert "Послуги для орендодавців" in res3.response_text

    # 4. Unrecognized query in LR2: MUST return LR2 Fallback and NEVER invoke RAG!
    up4 = TelegramUpdateMock(
        update_id=910004,
        message={"chat": {"id": 11111}, "text": "Яка вартість однокімнатної квартири на Сихові?"}
    )
    res4 = await process_telegram_update(up4, db=db_session)
    assert res4.route == "LR2 Fallback"
    assert "Не вдалося розпізнати запит" in res4.response_text
    assert res4.reply_markup is not None
    assert "keyboard" in res4.reply_markup
    assert res4.sources is None


@pytest.mark.anyio
async def test_lr6_mode_isolation_and_rag(db_session):
    """
    In LR6 mode:
      - /start returns LR6 AI Greeting
      - Questions containing 'купівл' or 'оренд' route to AI RAG and are NOT intercepted by LR2!
    """
    set_telegram_bot_mode("LR6", db_session)

    # 1. Start command in LR6
    up1 = TelegramUpdateMock(
        update_id=920001,
        message={"chat": {"id": 22222}, "text": "/start"}
    )
    res1 = await process_telegram_update(up1, db=db_session)
    assert res1.route == "LR6 AI Greeting"
    assert "інформаційний помічник" in res1.response_text
    assert res1.reply_markup == {"remove_keyboard": True}

    # 2. Query containing 'купівлі' - in LR6, it MUST route to AI RAG, NOT LR2 static text!
    up2 = TelegramUpdateMock(
        update_id=920002,
        message={"chat": {"id": 22222}, "text": "Які документи потрібні при купівлі житла?"}
    )
    with patch("core.api.v1.endpoints.telegram.query_rag", new_callable=AsyncMock) as mock_rag:
        mock_rag.return_value = RAGQueryResponse(
            answer="Для купівлі житла необхідні паспорт, РНОКПП та правовстановлюючі документи.",
            sources=["realtor_guide.docx"],
            citations=[],
            confidence_score=0.92,
            latency_seconds=0.5,
            mode="grounded"
        )
        res2 = await process_telegram_update(up2, db=db_session)
        assert res2.route == "AI RAG Agent"
        assert "паспорт" in res2.response_text
        assert res2.sources == ["realtor_guide.docx"]
        mock_rag.assert_called_once()

    # 3. Query containing 'оренда' - in LR6, it MUST route to AI RAG, NOT LR2 static text!
    up3 = TelegramUpdateMock(
        update_id=920003,
        message={"chat": {"id": 22222}, "text": "Які умови в договорі оренди квартири?"}
    )
    with patch("core.api.v1.endpoints.telegram.query_rag", new_callable=AsyncMock) as mock_rag:
        mock_rag.return_value = RAGQueryResponse(
            answer="Типовий договір оренди містить строк оренди, розмір орендної плати та акт приймання-передачі.",
            sources=["lease_agreement_template.docx"],
            citations=[],
            confidence_score=0.95,
            latency_seconds=0.4,
            mode="grounded"
        )
        res3 = await process_telegram_update(up3, db=db_session)
        assert res3.route == "AI RAG Agent"
        assert "Типовий договір оренди" in res3.response_text
        mock_rag.assert_called_once()


@pytest.mark.anyio
async def test_admin_mode_switching_command(db_session):
    """
    Test admin command /mode lr2, /mode lr6, /mode status:
      - Admin chat can change mode directly
      - Non-admin chat cannot change mode
    """
    admin_id = getattr(settings, "TELEGRAM_ADMIN_CHAT_ID", "684809016")
    set_telegram_bot_mode("LR2", db_session)

    # 1. Admin switches to LR6
    up_admin = TelegramUpdateMock(
        update_id=930001,
        message={"chat": {"id": int(admin_id)}, "text": "/mode lr6"}
    )
    res_admin = await process_telegram_update(up_admin, db=db_session)
    assert res_admin.route == "Admin Command"
    assert "ЛР6" in res_admin.response_text
    assert res_admin.reply_markup == {"remove_keyboard": True}
    assert get_telegram_bot_mode(db_session) == "LR6"

    # 2. Admin checks status
    up_status = TelegramUpdateMock(
        update_id=930002,
        message={"chat": {"id": int(admin_id)}, "text": "/mode status"}
    )
    res_status = await process_telegram_update(up_status, db=db_session)
    assert res_status.route == "Admin Command"
    assert "LR6" in res_status.response_text

    # 3. Non-admin user tries /mode lr2
    # In LR6 mode, non-admin text is treated as normal RAG query or fallback
    up_user = TelegramUpdateMock(
        update_id=930003,
        message={"chat": {"id": 999999}, "text": "/mode lr2"}
    )
    with patch("core.api.v1.endpoints.telegram.query_rag", new_callable=AsyncMock) as mock_rag:
        mock_rag.return_value = RAGQueryResponse(
            answer="RAG response for text",
            sources=[],
            citations=[],
            confidence_score=0.5,
            latency_seconds=0.3,
            mode="grounded"
        )
        res_user = await process_telegram_update(up_user, db=db_session)
        # Verify non-admin was NOT treated as Admin Command
        assert res_user.route != "Admin Command"
        # Database mode remains LR6
        assert get_telegram_bot_mode(db_session) == "LR6"


@pytest.mark.anyio
async def test_repeated_admin_mode_command_does_not_repeat_keyboard_notification(db_session):
    admin_id = int(getattr(settings, "TELEGRAM_ADMIN_CHAT_ID", "684809016"))
    set_telegram_bot_mode("LR2", db_session)
    result = await process_telegram_update(
        TelegramUpdateMock(
            update_id=930004,
            message={"chat": {"id": admin_id}, "text": "/mode lr2"},
        ),
        db=db_session,
    )
    assert result.route == "Admin Command"
    assert "вже активний" in result.response_text
    assert result.reply_markup is None
