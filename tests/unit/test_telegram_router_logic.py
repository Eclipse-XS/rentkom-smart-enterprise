import json
import pytest

def simulate_router(message_text: str, rules_config: dict) -> str:
    rules = rules_config.get("rules", {}).get("values", [])
    t_clean = message_text.strip().lower()

    for rule in rules:
        cond = rule.get("conditions", {}).get("conditions", [])[0]
        right_val = cond.get("rightValue", "").lower()
        op = cond.get("operator", {}).get("operation")

        if op == "equals" and t_clean == right_val:
            return rule.get("outputKey")
        elif op == "contains" and right_val in t_clean:
            return rule.get("outputKey")

    return rules_config.get("options", {}).get("renameFallbackOutput", "extra")

def test_unified_telegram_router_routes():
    with open("SMART_ENTERPRISE/automation/n8n/unified_telegram_router.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    router_node = next(n for n in data["nodes"] if n["id"] == "u-node-switch")
    rules_config = router_node["parameters"]

    # 1. Start command
    res1 = simulate_router("/start", rules_config)
    assert res1 == "Start Menu"

    # 2. Buying menu
    res2 = simulate_router("🏢 Процес купівлі/продажу", rules_config)
    assert res2 == "Купівля/Продаж"

    # 3. Landlord menu
    res3 = simulate_router("🔑 Послуги для орендодавців", rules_config)
    assert res3 == "Орендодавцям"

    # 4. Natural language query (routes to AI RAG)
    res4 = simulate_router("Які документи перевіряються перед покупкою?", rules_config)
    assert res4 == "AI RAG запит"

    # 5. Fallback verification
    fallback_node = next(n for n in data["nodes"] if n["id"] == "u-node-send-ai-response")
    fallback_expr = fallback_node["parameters"].get("text") or fallback_node["parameters"].get("jsonBody", "")
    assert "Вибачте, сервіс консультацій тимчасово недоступний" in fallback_expr
    assert "+38 (032) 240-00-00" in fallback_expr
