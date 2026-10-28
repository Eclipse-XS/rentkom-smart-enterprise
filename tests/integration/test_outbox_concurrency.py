import os
import tempfile
import threading
import time
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from core.models import Base, NotificationOutbox, NotificationAttempt
from core.services.outbox_service import process_outbox

def test_concurrent_outbox_workers_no_duplicates():
    # File-based SQLite DB with WAL mode so threads have independent DB-API connections
    db_file = os.path.join(tempfile.gettempdir(), f"test_outbox_concurrency_{int(time.time()*1000)}.db")
    if os.path.exists(db_file):
        try:
            os.remove(db_file)
        except OSError:
            pass

    engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"timeout": 30},
        echo=False
    )
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL;"))
        conn.commit()

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    # Pre-populate 10 outbox records
    init_db = Session()
    for i in range(10):
        item = NotificationOutbox(
            channel="EMAIL",
            recipient=f"client_{i}@rentkom.lviv.ua",
            subject=f"Підтвердження #{i}",
            payload_json={"id": i},
            status="PENDING"
        )
        init_db.add(item)
    init_db.commit()
    init_db.close()

    results = []

    def worker_job(worker_id):
        worker_db = Session()
        try:
            res = process_outbox(worker_db, batch_size=10)
            results.append((worker_id, res))
        finally:
            worker_db.close()

    # Run 2 concurrent worker threads competing for the same 10 records
    t1 = threading.Thread(target=worker_job, args=("worker-1",))
    t2 = threading.Thread(target=worker_job, args=("worker-2",))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Verify final state
    verify_db = Session()
    all_outbox = verify_db.query(NotificationOutbox).all()
    all_attempts = verify_db.query(NotificationAttempt).all()

    # 1. Total processed across workers equals 10 (no double processing)
    total_processed = sum(r[1].processed_count for r in results)
    total_success = sum(r[1].success_count for r in results)
    assert total_processed == 10
    assert total_success == 10

    # 2. Every single record must be MOCK_DELIVERED (or PROVIDER_ACCEPTED/SENT)
    assert all(item.status in ("MOCK_DELIVERED", "PROVIDER_ACCEPTED", "SENT") for item in all_outbox)

    # 3. No record has more than 1 attempt
    assert len(all_attempts) == 10
    for item in all_outbox:
        assert len(item.attempts) == 1
        assert item.attempts[0].status == "SUCCESS"

    verify_db.close()
    engine.dispose()
    try:
        os.remove(db_file)
    except OSError:
        pass
