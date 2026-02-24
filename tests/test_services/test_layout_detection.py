"""
Tests for layout detection service.

Covers all 4 detection layers + mismatch classification.
"""

import pytest

from src.services.layout_detection import (
    detect_layout,
    detect_layout_text,
    detect_layout_brand_model,
    detect_layout_ean,
    detect_layout_cross_market,
    classify_mismatch,
    EXPECTED_LAYOUT,
    DOMAINS,
)


class TestDetectLayoutText:
    """Layer 1: Title keyword detection."""

    def test_detect_qwertz_from_title(self):
        layout, layer = detect_layout_text("QWERTZ Tastatur kabellos")
        assert layout == "qwertz"
        assert layer == "title_keyword"

    def test_detect_azerty_from_title(self):
        layout, layer = detect_layout_text("clavier azerty sans fil")
        assert layout == "azerty"
        assert layer == "title_keyword"

    def test_detect_qwerty_uk_from_title(self):
        layout, layer = detect_layout_text("Wireless keyboard UK Layout")
        assert layout == "qwerty_uk"
        assert layer == "title_keyword"

    def test_detect_nothing_from_generic_title(self):
        layout, layer = detect_layout_text("USB cable 3m premium")
        assert layout is None
        assert layer == ""


class TestDetectLayoutBrandModel:
    """Layer 2: Known brand+model detection."""

    def test_detect_cherry_kc_1000(self):
        layout, layer = detect_layout_brand_model("KC 1000 Wired", "Cherry")
        assert layout == "qwertz"
        assert layer == "brand_model_db"

    def test_detect_logitech_k120_de(self):
        layout, layer = detect_layout_brand_model("K120 DE USB", "Logitech")
        assert layout == "qwertz"
        assert layer == "brand_model_db"

    def test_no_match_for_unknown_model(self):
        layout, layer = detect_layout_brand_model("Random Model X", "Acme")
        assert layout is None
        assert layer == ""


class TestDetectLayoutEan:
    """Layer 3: EAN prefix detection."""

    def test_detect_german_ean(self):
        layout, layer = detect_layout_ean("4025112091001")
        assert layout == "qwertz"
        assert layer == "ean_prefix"

    def test_no_match_for_us_ean(self):
        layout, layer = detect_layout_ean("0012345678905")
        assert layout is None

    def test_no_match_for_empty_ean(self):
        layout, layer = detect_layout_ean("")
        assert layout is None

    def test_no_match_for_short_ean(self):
        layout, layer = detect_layout_ean("40")
        assert layout is None


class TestDetectLayoutCrossMarket:
    """Layer 4: Cross-market presence detection."""

    def test_detect_de_plus_uk(self):
        layout, layer = detect_layout_cross_market({"DE", "UK"})
        assert layout == "qwertz"
        assert layer == "cross_market"

    def test_no_match_for_de_only(self):
        layout, layer = detect_layout_cross_market({"DE"})
        assert layout is None

    def test_no_match_for_empty(self):
        layout, layer = detect_layout_cross_market(set())
        assert layout is None


class TestDetectLayout:
    """Integration: full multi-layer detect_layout()."""

    def test_detect_qwertz_from_title_entry(self):
        result = detect_layout({"title": "QWERTZ Tastatur"})
        assert result["detected_layout"] == "qwertz"
        assert result["confidence"] == "high"
        assert result["detection_layer"] == "title_keyword"

    def test_detect_from_brand_model(self):
        result = detect_layout({"title": "KC 1000 Wired", "brand": "Cherry"})
        assert result["detected_layout"] == "qwertz"
        assert result["confidence"] == "high"
        assert result["detection_layer"] == "brand_model_db"

    def test_detect_from_ean(self):
        result = detect_layout({"title": "Keyboard", "ean": "4001234567890"})
        assert result["detected_layout"] == "qwertz"
        assert result["confidence"] == "medium"
        assert result["detection_layer"] == "ean_prefix"

    def test_detect_unknown(self):
        result = detect_layout({"title": "USB cable"})
        assert result["detected_layout"] == "unknown"
        assert result["confidence"] == "none"
        assert result["detection_layer"] == "none"

    def test_detect_from_market_specific_title(self):
        result = detect_layout({"title": "", "title_DE": "Deutsche Tastatur QWERTZ"})
        assert result["detected_layout"] == "qwertz"
        assert result["confidence"] == "high"

    def test_present_markets_as_string(self):
        result = detect_layout({"title": "Keyboard", "present_markets": "DE,UK"})
        assert result["detected_layout"] == "qwertz"
        assert result["confidence"] == "low"
        assert result["detection_layer"] == "cross_market"


class TestClassifyMismatch:
    """Mismatch classification."""

    def test_qwertz_on_uk_is_mismatch(self):
        is_mismatch, label = classify_mismatch("qwertz", "UK")
        assert is_mismatch is True
        assert label == "qwertz"

    def test_qwertz_on_de_is_no_mismatch(self):
        is_mismatch, label = classify_mismatch("qwertz", "DE")
        assert is_mismatch is False
        assert label == "qwertz"

    def test_azerty_on_fr_is_no_mismatch(self):
        is_mismatch, label = classify_mismatch("azerty", "FR")
        assert is_mismatch is False
        assert label == "azerty"

    def test_azerty_on_de_is_mismatch(self):
        is_mismatch, label = classify_mismatch("azerty", "DE")
        assert is_mismatch is True
        assert label == "azerty"

    def test_unknown_is_not_mismatch(self):
        is_mismatch, label = classify_mismatch("unknown", "UK")
        assert is_mismatch is False
        assert label == "unknown"

    def test_unknown_market_is_not_mismatch(self):
        is_mismatch, label = classify_mismatch("qwertz", "XX")
        assert is_mismatch is False
        assert label == "unknown"
