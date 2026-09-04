"""Manual card assertions shared by strict-stack and full-coverage probes."""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Mapping


def cards(message: Mapping[str, Any]) -> list[dict[str, Any]]:
    card = message.get("ui_card") or {}
    if card.get("type") == "card_group":
        return [dict(item) for item in (card.get("items") or [])
                if isinstance(item, dict)]
    return [dict(card)] if card else []


def comparable(value: object) -> str:
    return re.sub(
        r"\s+", "", unicodedata.normalize("NFKC", str(value or "")).casefold(),
    )


def manual_card_errors(
    card: Mapping[str, Any],
    expected: Mapping[str, Any],
    approved_document: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    prov = card.get("_prov") or {}
    document = card.get("document") or {}
    chunks = [item for item in (card.get("chunks") or [])
              if isinstance(item, dict)]
    images = [item for item in (card.get("images") or [])
              if isinstance(item, dict)]
    pages = [int(item["page_start"]) for item in chunks if item.get("page_start")]
    page_set = set(pages)

    if (prov.get("mode") != "real"
            or prov.get("vendor") != "xiaomi-su7-2024-user-manual"):
        errors.append("manual provenance is not the approved real provider")
    for key in (
        "vehicle_model", "revision", "source_sha256", "content_sha256",
        "visual_assets_sha256",
    ):
        if document.get(key) != approved_document.get(key):
            errors.append(f"document {key} mismatch")

    if expected.get("expect_empty"):
        if chunks:
            errors.append(f"expected empty chunks, got pages {pages}")
        if images:
            errors.append("expected empty images")
        return errors

    sources = card.get("sources") or []
    if not any("PDF第" in str(source) for source in sources):
        errors.append("manual card has no PDF page citation")
    expected_top = expected.get("expect_top_page")
    if expected_top is not None and (not pages or pages[0] != int(expected_top)):
        errors.append(f"wrong top page: want {expected_top}, got {pages[:1]}")
    expected_any = {int(page) for page in expected.get("expect_pages_any") or []}
    if expected_any and not expected_any.intersection(page_set):
        errors.append(
            f"missing expected page: want any {sorted(expected_any)}, got {pages}")
    expected_all = {int(page) for page in expected.get("expect_pages_all") or []}
    if not expected_all.issubset(page_set):
        errors.append(f"missing required pages: {sorted(expected_all - page_set)}")

    expected_section = tuple(
        str(item) for item in expected.get("expect_section_path") or [])
    if expected_section:
        actual_sections = {
            tuple(str(part) for part in item.get("section_path") or [])
            for item in chunks
        }
        if expected_section not in actual_sections:
            rendered = sorted(" > ".join(path) for path in actual_sections)
            errors.append(
                f"missing expected section {' > '.join(expected_section)!r}, "
                f"got {rendered}")
    expected_section_terms = [
        comparable(item)
        for item in expected.get("expect_section_terms_all") or []
    ]
    if expected_section_terms and not any(
            all(term in comparable(" > ".join(
                str(part) for part in item.get("section_path") or []))
                for term in expected_section_terms)
            for item in chunks):
        errors.append(
            "missing expected section terms: "
            f"{expected.get('expect_section_terms_all')}")

    combined = comparable(
        "\n".join(str(item.get("content") or "") for item in chunks))
    missing_text = [
        str(term) for term in expected.get("expect_text_all") or []
        if comparable(term) not in combined
    ]
    if missing_text:
        errors.append(f"missing text: {missing_text}")
    alternatives = [str(term) for term in expected.get("expect_text_any") or []]
    if alternatives and not any(comparable(term) in combined for term in alternatives):
        errors.append(f"missing any text: {alternatives}")

    image_pages = {int(item["page_start"]) for item in images
                   if item.get("page_start")}
    expected_image_pages = {
        int(page) for page in expected.get("expect_image_pages_any") or []
    }
    if expected_image_pages and not expected_image_pages.intersection(image_pages):
        errors.append(
            f"missing expected image page: want any {sorted(expected_image_pages)}, "
            f"got {sorted(image_pages)}")
    captions = comparable(
        "\n".join(str(item.get("caption") or "") for item in images))
    missing_captions = [
        str(caption) for caption in expected.get("expect_image_caption_all") or []
        if comparable(caption) not in captions
    ]
    if missing_captions:
        errors.append(f"missing image captions: {missing_captions}")
    for image in images:
        data_uri = str(image.get("data_uri") or "")
        if not data_uri.startswith((
                "data:image/png;base64,", "data:image/jpeg;base64,")):
            errors.append("manual image is not a trusted inline PNG/JPEG")
            break
        if re.fullmatch(r"[0-9a-f]{64}", str(image.get("sha256") or "")) is None:
            errors.append("manual image SHA-256 is invalid")
            break
    return errors


def manual_response_errors(
    message: Mapping[str, Any],
    expected: Mapping[str, Any],
    approved_document: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    if message.get("actions"):
        errors.append("manual probe returned actions")
    if message.get("need_confirm"):
        errors.append("manual probe requested confirmation")
    response_cards = cards(message)
    manual_cards = [card for card in response_cards if card.get("type") == "manual"]
    other_types = [str(card.get("type") or "") for card in response_cards
                   if card.get("type") != "manual"]
    if len(manual_cards) != 1:
        errors.append(f"expected exactly one manual card, got {len(manual_cards)}")
    if other_types:
        errors.append(f"manual response included other card types: {other_types}")
    if len(manual_cards) == 1:
        errors.extend(manual_card_errors(
            manual_cards[0], expected, approved_document))
    return errors
