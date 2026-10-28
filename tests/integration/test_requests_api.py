def test_create_and_get_request_api(client):
    payload = {
        "full_name": "Маркіян Дуб",
        "phone": "+380933334455",
        "email": "mark@test.com",
        "request_purpose": "Хочу продати",
        "property_type": "Будинок",
        "property_location": "Личаківський р-н",
        "consultation_date": "2026-11-15",
        "additional_comments": "Терміново"
    }

    # 1. POST /api/v1/requests
    resp = client.post("/api/v1/requests/", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] is not None
    assert data["request_purpose"] == "Хочу продати"
    assert data["status"] == "NEW"
    req_id = data["id"]

    # 2. GET /api/v1/requests/{id}
    get_resp = client.get(f"/api/v1/requests/{req_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["property_type"] == "Будинок"

    # 3. PATCH /api/v1/requests/{id}/status
    patch_resp = client.patch(
        f"/api/v1/requests/{req_id}/status",
        json={"new_status": "SCHEDULED", "comment": "Узгоджено час зустрічі", "changed_by": "manager_1"}
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["status"] == "SCHEDULED"

def test_idempotent_request_submission(client):
    payload = {
        "full_name": "Софія Бойко",
        "phone": "+380674445566",
        "request_purpose": "Хочу здати в оренду",
        "property_type": "Квартира",
        "property_location": "Галицький р-н",
        "consultation_date": "2026-11-20",
        "source_row_id": "google_sheets_v2:row_123"
    }

    # First submit
    resp1 = client.post("/api/v1/requests/", json=payload)
    assert resp1.status_code == 201
    id1 = resp1.json()["id"]

    # Second submit with identical source_row_id
    resp2 = client.post("/api/v1/requests/", json=payload)
    assert resp2.status_code == 201
    id2 = resp2.json()["id"]

    # Must be the exact same request without duplication
    assert id1 == id2
