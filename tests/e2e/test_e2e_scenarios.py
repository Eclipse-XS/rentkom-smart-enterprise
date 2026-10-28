def test_e2e_full_business_pipeline(client):
    # Scenario A: Ingress Request -> Outbox Enqueued
    req_payload = {
        "full_name": "Тарас Шевченко",
        "phone": "+380675556677",
        "email": "taras@shev.ua",
        "request_purpose": "Хочу купити",
        "property_type": "Квартира",
        "property_location": "м. Львів, Франківський р-н",
        "consultation_date": "2026-11-25",
        "source_channel": "TELEGRAM"
    }
    r = client.post("/api/v1/requests/", json=req_payload)
    assert r.status_code == 201
    req_id = r.json()["id"]

    # Verify outbox has 1 PENDING item
    outbox_resp = client.get("/api/v1/notifications/outbox?status=PENDING")
    assert outbox_resp.status_code == 200
    pending_items = outbox_resp.json()
    assert len(pending_items) >= 1
    assert any(item["payload_json"]["request_id"] == req_id for item in pending_items)

    # Process outbox (Simulated reliable dispatch)
    flush_resp = client.post("/api/v1/notifications/process")
    assert flush_resp.status_code == 200
    flush_data = flush_resp.json()
    assert flush_data["success_count"] >= 1

    # Verify outbox item is now SENT
    sent_resp = client.get("/api/v1/notifications/outbox?status=SENT")
    assert sent_resp.status_code == 200
    assert any(item["payload_json"]["request_id"] == req_id for item in sent_resp.json())

    # Update status to COMPLETED
    patch_resp = client.patch(
        f"/api/v1/requests/{req_id}/status",
        json={"new_status": "COMPLETED", "comment": "Угода успішно підписана"}
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["status"] == "COMPLETED"

    # Verify Analytics Summary reflects the completed request
    summary = client.get("/api/v1/analytics/summary").json()
    assert summary["requests_by_status"].get("COMPLETED", 0) >= 1
