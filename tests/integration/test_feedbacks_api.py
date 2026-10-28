def test_feedback_submission_and_analytics(client):
    # 1. Submit 2 feedbacks
    fb1 = {
        "client_identifier": "+380671112233",
        "service_rating": 5,
        "realtor_rating": 5,
        "feedback_text": "Чудова консультація!"
    }
    fb2 = {
        "client_identifier": "+380502223344",
        "service_rating": 4,
        "realtor_rating": 3,
        "feedback_text": "Все добре."
    }
    r1 = client.post("/api/v1/feedbacks/", json=fb1)
    assert r1.status_code == 201
    r2 = client.post("/api/v1/feedbacks/", json=fb2)
    assert r2.status_code == 201

    # 2. Query analytics summary
    analytics_resp = client.get("/api/v1/analytics/summary")
    assert analytics_resp.status_code == 200
    summary = analytics_resp.json()
    assert summary["total_feedbacks"] == 2
    assert summary["average_service_rating"] == 4.5
    assert summary["average_realtor_rating"] == 4.0
