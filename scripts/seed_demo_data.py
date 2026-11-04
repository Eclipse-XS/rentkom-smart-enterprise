# -*- coding: utf-8 -*-
"""
Rentkom Smart Enterprise: Explicit Demo Data Seeding Script (DATA-03).
Must only be invoked on demand in demo/development environments.
Refuses to execute in production mode.
"""
import sys
import logging
from datetime import date, datetime, timezone
from sqlalchemy.orm import Session
from core.config import settings
from core.database import SessionLocal, engine, Base
from core.models import Customer, ServiceRequest, CustomerFeedback

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("seed_demo_data")

DEMO_CUSTOMERS = [
    {"full_name": "Олексій Мельник", "phone": "+380671112233", "email": "oleksiy@test.ua"},
    {"full_name": "Ірина Ковальчук", "phone": "+380502223344", "email": "irina@test.ua"},
    {"full_name": "Маркіян Дуб", "phone": "+380933334455", "email": "markiian@test.ua"},
    {"full_name": "Софія Бойко", "phone": "+380674445566", "email": "sofiya@test.ua"},
    {"full_name": "Василь Гончарук", "phone": "+380689998877", "email": "vasyl@test.ua"},
]

DEMO_REQUESTS = [
    {
        "phone": "+380671112233",
        "request_purpose": "Хочу купити",
        "property_type": "Квартира",
        "property_location": "Франківський р-н, вул. Генерала Чупринки",
        "consultation_date": date(2026, 11, 20),
        "additional_comments": "Потрібен 2-кімнатний варіант біля Політехніки",
        "status": "COMPLETED",
        "source_channel": "GOOGLE_FORMS",
        "source_row_id": "seed:row:1"
    },
    {
        "phone": "+380502223344",
        "request_purpose": "Хочу орендувати",
        "property_type": "Квартира",
        "property_location": "Сихівський р-н, проспект Червоної Калини",
        "consultation_date": date(2026, 11, 21),
        "additional_comments": "Оренда для сім'ї, довгостроково",
        "status": "COMPLETED",
        "source_channel": "GOOGLE_FORMS",
        "source_row_id": "seed:row:2"
    },
    {
        "phone": "+380933334455",
        "request_purpose": "Хочу продати",
        "property_type": "Будинок",
        "property_location": "Личаківський р-н, Винники",
        "consultation_date": date(2026, 11, 22),
        "additional_comments": "Терміновий продаж будинку 150 кв.м",
        "status": "COMPLETED",
        "source_channel": "GOOGLE_FORMS",
        "source_row_id": "seed:row:3"
    },
    {
        "phone": "+380674445566",
        "request_purpose": "Хочу здати в оренду",
        "property_type": "Квартира",
        "property_location": "Галицький р-н, площа Ринок",
        "consultation_date": date(2026, 11, 23),
        "additional_comments": "Квартира з ремонтом під подобову/довгострокову",
        "status": "COMPLETED",
        "source_channel": "GOOGLE_FORMS",
        "source_row_id": "seed:row:4"
    },
    {
        "phone": "+380689998877",
        "request_purpose": "Хочу купити",
        "property_type": "Квартира",
        "property_location": "м. Львів, Шевченківський р-н",
        "consultation_date": date(2026, 11, 24),
        "additional_comments": "Консультація щодо купівлі нерухомості",
        "status": "COMPLETED",
        "source_channel": "GOOGLE_FORMS",
        "source_row_id": "seed:row:5"
    },
]

DEMO_FEEDBACKS = [
    {
        "client_identifier": "+380671112233",
        "service_rating": 5,
        "realtor_rating": 5,
        "feedback_text": "Чудовий супровід угоди, все швидко та професійно!",
        "source_channel": "GOOGLE_FORMS",
        "source_row_id": "seed:feedback:1"
    },
    {
        "client_identifier": "+380502223344",
        "service_rating": 4,
        "realtor_rating": 5,
        "feedback_text": "Задоволена орендою квартири на Сихові.",
        "source_channel": "GOOGLE_FORMS",
        "source_row_id": "seed:feedback:2"
    }
]


def seed_demo_data(db: Session, force: bool = False) -> dict:
    """Populates explicit demo data into database. Refuses in production."""
    env = (settings.ENVIRONMENT or "").strip().lower()
    if env == "production" and not force:
        raise RuntimeError("CRITICAL: Refusing to seed demo data into PRODUCTION environment!")

    logger.info("Starting explicit demo data seeding (environment: %s)...", env)
    try:
        bind = db.get_bind()
        Base.metadata.create_all(bind=bind)
    except Exception:
        pass

    # 1. Customers
    cust_map = {}
    cust_created = 0
    for c in DEMO_CUSTOMERS:
        existing = db.query(Customer).filter_by(phone=c["phone"]).first()
        if not existing:
            cust = Customer(
                full_name=c["full_name"],
                phone=c["phone"],
                email=c["email"],
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            db.add(cust)
            db.flush()
            cust_map[c["phone"]] = cust.id
            cust_created += 1
        else:
            cust_map[c["phone"]] = existing.id

    # 2. Service Requests
    req_count = 0
    for r in DEMO_REQUESTS:
        cid = cust_map.get(r["phone"])
        existing = db.query(ServiceRequest).filter_by(source_row_id=r["source_row_id"]).first()
        if not existing and cid:
            req = ServiceRequest(
                customer_id=cid,
                request_purpose=r["request_purpose"],
                property_type=r["property_type"],
                property_location=r["property_location"],
                consultation_date=r["consultation_date"],
                additional_comments=r["additional_comments"],
                status=r["status"],
                source_channel=r["source_channel"],
                source_row_id=r["source_row_id"],
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            db.add(req)
            req_count += 1

    # 3. Feedbacks
    fb_count = 0
    for fb in DEMO_FEEDBACKS:
        existing = db.query(CustomerFeedback).filter_by(source_row_id=fb["source_row_id"]).first()
        if not existing:
            cid = cust_map.get(fb["client_identifier"])
            item = CustomerFeedback(
                customer_id=cid,
                client_identifier=fb["client_identifier"],
                service_rating=fb["service_rating"],
                realtor_rating=fb["realtor_rating"],
                feedback_text=fb["feedback_text"],
                source_channel=fb["source_channel"],
                source_row_id=fb["source_row_id"],
                created_at=datetime.now(timezone.utc)
            )
            db.add(item)
            fb_count += 1

    db.commit()
    logger.info("Demo seeding completed: %d customers, %d requests, %d feedbacks.", len(cust_map), req_count, fb_count)
    return {
        "customers_created": cust_created,
        "requests_created": req_count,
        "feedbacks_created": fb_count,
        "customers_seeded": len(cust_map),
        "requests_seeded": req_count,
        "feedbacks_seeded": fb_count,
    }


if __name__ == "__main__":
    with SessionLocal() as session:
        seed_demo_data(session)
