from support.manual_rag_contract import (
    manual_card_errors,
    manual_response_errors,
)


DOCUMENT = {
    "vehicle_model": "xiaomi-su7-2024",
    "revision": "2024-04-15",
    "source_sha256": "s" * 64,
    "content_sha256": "c" * 64,
    "visual_assets_sha256": "v" * 64,
}


def _card(section=("驾驶和操作", "灯光", "后雾灯")):
    return {
        "type": "manual",
        "_prov": {
            "mode": "real",
            "vendor": "xiaomi-su7-2024-user-manual",
        },
        "document": dict(DOCUMENT),
        "sources": ["SU7用户手册 · PDF第89页"],
        "chunks": [{
            "page_start": 89,
            "page_end": 89,
            "section_path": list(section),
            "content": "后雾灯可以在灯光设置中控制。",
        }],
        "images": [],
    }


def test_card_contract_checks_exact_section_path():
    expected = {
        "expect_pages_any": [89],
        "expect_section_path": ["驾驶和操作", "灯光", "后雾灯"],
    }

    assert manual_card_errors(_card(), expected, DOCUMENT) == []
    errors = manual_card_errors(
        _card(("驾驶和操作", "灯光", "近光灯")), expected, DOCUMENT)
    assert any("missing expected section" in error for error in errors)


def test_response_contract_rejects_confirmation_even_without_actions():
    errors = manual_response_errors(
        {"ui_card": _card(), "actions": [], "need_confirm": True},
        {"expect_pages_any": [89]},
        DOCUMENT,
    )

    assert "manual probe requested confirmation" in errors


def test_card_contract_accepts_atomic_outline_terms_inside_combined_path():
    combined = _card((
        "信息显示和娱乐",
        "空调控制",
        "车内高温保护 / 个性化娱乐 > 地图和导航",
    ))
    expected = {
        "expect_pages_any": [89],
        "expect_section_terms_all": [
            "信息显示和娱乐", "个性化娱乐", "地图和导航",
        ],
    }

    assert manual_card_errors(combined, expected, DOCUMENT) == []
