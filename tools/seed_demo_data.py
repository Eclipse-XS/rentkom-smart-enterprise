import os
import sys
import random

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from datetime import datetime, timedelta, date, timezone
from sqlalchemy.orm import Session
from core.database import SessionLocal, Base, engine
from core.models import (
    Customer, ServiceRequest, RequestStatusHistory,
    CustomerFeedback, NotificationOutbox, NotificationAttempt, AuditEvent
)
from core.services.request_service import normalize_phone

LVIV_LOCATIONS = [
    "м. Львів, Франківський р-н, вул. Генерала Чупринки",
    "м. Львів, Сихівський р-н, просп. Червоної Калини",
    "м. Львів, Личаківський р-н, вул. Личаківська",
    "м. Львів, Галицький р-н, пл. Ринок",
    "м. Львів, Шевченківський р-н, вул. В'ячеслава Чорновола",
    "м. Львів, Залізничний р-н, вул. Городоцька",
    "м. Львів, Франківський р-н, вул. Наукова",
    "м. Львів, Сихівський р-н, вул. Сихівська",
    "м. Львів, Шевченківський р-н, вул. Мазепи",
    "м. Львів, Личаківський р-н, м. Винники"
]

CLIENT_NAMES = [
    ("Олексій Мельник", "+380671112233", "oleksiy.melnyk@gmail.com"),
    ("Ірина Ковальчук", "+380502223344", "irina.koval@ukr.net"),
    ("Маркіян Дуб", "+380933334455", "markiyan.dub@gmail.com"),
    ("Софія Бойко", "+380674445566", "sofia.boyko@yahoo.com"),
    ("Василь Гончарук", "+380689998877", "vasyl.goncharuk@gmail.com"),
    ("Тарас Шевченко", "+380675556677", "taras.shev@gmail.com"),
    ("Оксана Бондар", "+380506667788", "oxana.bondar@ukr.net"),
    ("Андрій Кравченко", "+380937778899", "andriy.krav@gmail.com"),
    ("Наталія Мороз", "+380678889900", "natali.moroz@gmail.com"),
    ("Юрій Романюк", "+380509990011", "yuriy.romanyuk@gmail.com"),
    ("Христина Павленко", "+380931110022", "khrystyna.p@ukr.net"),
    ("Богдан Ткач", "+380672221133", "bohdan.tkach@gmail.com"),
    ("Олена Литвин", "+380503332244", "olena.lytvyn@gmail.com"),
    ("Роман Кравчук", "+380934443355", "roman.kravchuk@ukr.net"),
    ("Марія Козак", "+380675554466", "maria.kozak@gmail.com"),
    ("Дмитро Савченко", "+380506665577", "dmytro.sav@gmail.com"),
    ("Вікторія Кузьма", "+380937776688", "viktoria.k@gmail.com"),
    ("Ярослав Швець", "+380678887799", "yaroslav.shvets@ukr.net"),
    ("Юлія Поліщук", "+380509998800", "yulia.polishchuk@gmail.com"),
    ("Максим Демченко", "+380931234567", "maksym.demchenko@gmail.com")
]

PURPOSES = ["Хочу купити", "Хочу орендувати", "Хочу продати", "Хочу здати в оренду"]
PROPERTY_TYPES = ["Квартира", "Будинок", "Комерційне приміщення", "Земля"]
STATUSES = ["NEW", "IN_REVIEW", "SCHEDULED", "COMPLETED", "CANCELLED"]

FEEDBACK_COMMENTS = [
    ("Відмінна робота! Швидко підібрали квартиру у Франківському районі.", 5, 5),
    ("Професійний супровід угоди купівлі-продажу, ретельно перевірили документи.", 5, 5),
    ("Дуже задоволений орендою офісу, рієлтор завжди був на зв'язку.", 5, 4),
    ("Все пройшло добре, але нотаріальне оформлення зайняло трохи більше часу.", 4, 4),
    ("Хороший сервіс, адекватні комісійні, рекомендую друзям.", 5, 5),
    ("Орендарі знайшлися за 3 дні, договір чіткий і прозорий.", 5, 5),
    ("Трохи довго чекали на відповідь менеджера у вихідний день, але результат чудовий.", 4, 3),
    ("Допомогли з оцінкою будинку у Винниках, дуже ввічливий персонал.", 5, 5),
    ("Зручний Telegram-бот, одразу дізнався всі кроки купівлі квартири.", 5, 5),
    ("Консультація юриста агентства зняла всі сумніви щодо забудовника.", 5, 5)
]

def seed_demo_data(db: Session = None, simulate_failure_at_request: int = None) -> dict:
    """
    Deterministic and idempotent demo data seeder.
    Uses natural unique keys (phone, source_row_id) for safe UPSERT.
    Survives partial interruptions without duplicates and preserves non-demo data.
    """
    should_close = False
    if db is None:
        Base.metadata.create_all(bind=engine)
        db = SessionLocal()
        should_close = True

    try:
        # Fixed random seed for total reproducibility
        random.seed(42)

        base_time = datetime(2026, 10, 1, 9, 0, 0, tzinfo=timezone.utc)
        stats = {
            "customers_created": 0,
            "customers_updated": 0,
            "requests_created": 0,
            "requests_updated": 0,
            "feedbacks_created": 0,
            "outbox_created": 0
        }

        # 1. Upsert customers
        customer_map = {}
        for idx, (name, phone, email) in enumerate(CLIENT_NAMES):
            norm_p = normalize_phone(phone)
            cust = db.query(Customer).filter(Customer.phone == norm_p).first()
            if not cust:
                cust = Customer(
                    full_name=name,
                    phone=norm_p,
                    email=email,
                    created_at=base_time + timedelta(days=idx % 5)
                )
                db.add(cust)
                db.flush()
                stats["customers_created"] += 1
            else:
                cust.full_name = name
                cust.email = email
                stats["customers_updated"] += 1
            customer_map[idx] = cust

        # 2. Upsert 25 service requests
        for i in range(25):
            if simulate_failure_at_request is not None and i == simulate_failure_at_request:
                db.commit()
                raise RuntimeError(f"Simulated interruption at request index {i}")

            cust = customer_map[i % len(CLIENT_NAMES)]
            status = STATUSES[random.choices([0, 1, 2, 3, 4], weights=[0.25, 0.20, 0.20, 0.30, 0.05])[0]]
            req_time = base_time + timedelta(days=i, hours=random.randint(8, 18), minutes=random.randint(0, 59))
            consult_d = (req_time + timedelta(days=random.randint(1, 4))).date()

            purpose = random.choice(PURPOSES)
            prop_type = random.choice(PROPERTY_TYPES)
            location = random.choice(LVIV_LOCATIONS)
            source_row = f"demo_seed_v2:{i+1}"

            req = db.query(ServiceRequest).filter(ServiceRequest.source_row_id == source_row).first()
            if not req:
                req = ServiceRequest(
                    customer_id=cust.id,
                    request_purpose=purpose,
                    property_type=prop_type,
                    property_location=location,
                    consultation_date=consult_d,
                    additional_comments=f"Демо-заявка #{i+1}: консультація щодо {prop_type.lower()}",
                    status=status,
                    source_channel=random.choice(["GOOGLE_FORMS", "TELEGRAM", "REST_API"]),
                    source_row_id=source_row,
                    created_at=req_time,
                    updated_at=req_time + timedelta(hours=random.randint(1, 48))
                )
                db.add(req)
                db.flush()
                stats["requests_created"] += 1

                # Add status history
                history_init = RequestStatusHistory(
                    request_id=req.id,
                    old_status=None,
                    new_status="NEW",
                    changed_by="system",
                    comment="Первинне надходження заявки",
                    created_at=req_time
                )
                db.add(history_init)

                if status != "NEW":
                    history_upd = RequestStatusHistory(
                        request_id=req.id,
                        old_status="NEW",
                        new_status=status,
                        changed_by="realtor_manager",
                        comment=f"Статус змінено на {status} після зв'язку з клієнтом",
                        created_at=req.updated_at
                    )
                    db.add(history_upd)
            else:
                # Idempotent update
                req.customer_id = cust.id
                req.request_purpose = purpose
                req.property_type = prop_type
                req.property_location = location
                req.consultation_date = consult_d
                stats["requests_updated"] += 1

            # Ensure notification outbox entry
            outbox_status = "SENT" if status in ["SCHEDULED", "COMPLETED", "IN_REVIEW"] else "PENDING"
            existing_outbox = db.query(NotificationOutbox).filter(
                NotificationOutbox.recipient == (cust.email or f"client_{cust.id}@rentkom.ua"),
                NotificationOutbox.subject.like(f"%#{req.id})")
            ).first()

            if not existing_outbox:
                outbox_item = NotificationOutbox(
                    channel="EMAIL",
                    recipient=cust.email or f"client_{cust.id}@rentkom.ua",
                    subject=f"Підтвердження консультації: Агентство нерухомості «Рентком» (#{req.id})",
                    payload_json={
                        "request_id": req.id,
                        "customer_name": cust.full_name,
                        "purpose": req.request_purpose,
                        "property_type": req.property_type,
                        "location": req.property_location,
                        "consultation_date": str(req.consultation_date)
                    },
                    status=outbox_status,
                    retry_count=0,
                    created_at=req_time,
                    sent_at=req_time + timedelta(minutes=2) if outbox_status == "SENT" else None
                )
                db.add(outbox_item)
                db.flush()
                stats["outbox_created"] += 1

                if outbox_status == "SENT":
                    attempt = NotificationAttempt(
                        outbox_id=outbox_item.id,
                        attempt_number=1,
                        status="SUCCESS",
                        response_code=250,
                        error_message=None,
                        attempted_at=req_time + timedelta(minutes=2)
                    )
                    db.add(attempt)

        # 3. Upsert 30 customer feedbacks
        for i in range(30):
            comment_text, serv_r, real_r = random.choice(FEEDBACK_COMMENTS)
            actual_serv = max(1, min(5, serv_r + random.choice([0, 0, -1, 0])))
            actual_real = max(1, min(5, real_r + random.choice([0, 0, 0, -1])))
            fb_time = base_time + timedelta(days=random.randint(1, 24), hours=random.randint(9, 20))
            cust = customer_map[random.randint(0, len(CLIENT_NAMES) - 1)]
            fb_source = f"demo_feedback:{i+1}"

            fb = db.query(CustomerFeedback).filter(CustomerFeedback.source_row_id == fb_source).first()
            if not fb:
                fb = CustomerFeedback(
                    customer_id=cust.id,
                    client_identifier=cust.phone,
                    service_rating=actual_serv,
                    realtor_rating=actual_real,
                    feedback_text=comment_text,
                    source_row_id=fb_source,
                    source_channel="GOOGLE_FORMS",
                    created_at=fb_time
                )
                db.add(fb)
                stats["feedbacks_created"] += 1
            else:
                fb.service_rating = actual_serv
                fb.realtor_rating = actual_real
                fb.feedback_text = comment_text

        # 4. Upsert system audit events
        for evt_type in ["SYSTEM_STARTUP", "SYNCHRONIZATION_RUN", "METRICS_CALCULATION", "OUTBOX_DISPATCH"]:
            audit = db.query(AuditEvent).filter(
                AuditEvent.event_type == evt_type,
                AuditEvent.entity_type == "system"
            ).first()
            if not audit:
                audit = AuditEvent(
                    event_type=evt_type,
                    entity_type="system",
                    entity_id=None,
                    details_json={"environment": "demo", "status": "OK"},
                    created_at=datetime.now(timezone.utc) - timedelta(hours=random.randint(1, 100))
                )
                db.add(audit)

        db.commit()
        print(f"[OK] Demo seed completed: {stats}")
        return stats
    except Exception as e:
        db.rollback()
        raise e
    finally:
        if should_close:
            db.close()

if __name__ == "__main__":
    seed_demo_data()
