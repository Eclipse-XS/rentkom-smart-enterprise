import json
import pytest

def simulate_switch(message_text: str, rules_config: dict) -> str:
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


def test_unified_telegram_router_node_structure():
    with open("SMART_ENTERPRISE/automation/n8n/unified_telegram_router.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    node_ids = {n["id"] for n in data["nodes"]}
    # Verify presence of mode switch and separate routers
    assert "u-node-mode-switch" in node_ids
    assert "u-node-lr2-switch" in node_ids
    assert "u-node-lr6-switch" in node_ids
    assert "u-node-lr2-fallback" in node_ids


def test_lr2_router_branches():
    with open("SMART_ENTERPRISE/automation/n8n/unified_telegram_router.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    lr2_node = next(n for n in data["nodes"] if n["id"] == "u-node-lr2-switch")
    rules_config = lr2_node["parameters"]

    # 1. Start command
    assert simulate_switch("/start", rules_config) == "Start Menu"

    # 2. Buying menu
    assert simulate_switch("🏢 Процес купівлі/продажу", rules_config) == "Купівля/Продаж"
    assert simulate_switch("купівля квартири", rules_config) == "Купівля/Продаж"

    # 3. Landlord menu
    assert simulate_switch("🔑 Послуги для орендодавців", rules_config) == "Орендодавцям"
    assert simulate_switch("оренда", rules_config) == "Орендодавцям"

    # 4. In LR2 mode, an unrecognized query routes to LR2 Fallback, NOT AI RAG!
    assert simulate_switch("Які документи перевіряються перед покупкою?", rules_config) == "LR2 Fallback"
    assert simulate_switch("Привіт, яка вартість ваших послуг?", rules_config) == "LR2 Fallback"


def test_lr6_router_branches():
    with open("SMART_ENTERPRISE/automation/n8n/unified_telegram_router.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    lr6_node = next(n for n in data["nodes"] if n["id"] == "u-node-lr6-switch")
    rules_config = lr6_node["parameters"]

    # 1. Start command
    assert simulate_switch("/start", rules_config) == "LR6 Start Greeting"

    # 2. In LR6 mode, all natural language questions route to AI RAG!
    assert simulate_switch("Які документи перевіряються перед купівлею?", rules_config) == "AI RAG Запит"
    assert simulate_switch("Розкажіть про оренду", rules_config) == "AI RAG Запит"
    assert simulate_switch("Привіт, як оформити завдаток?", rules_config) == "AI RAG Запит"
