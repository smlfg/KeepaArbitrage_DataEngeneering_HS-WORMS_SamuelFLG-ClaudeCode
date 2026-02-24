"""
Layout Detection Service
========================
Multi-layer keyboard layout detection extracted from
scripts/collect_1000_keyboards.py for reuse in the
continuous ASIN discovery pipeline.

Layers (highest confidence first):
    1. Title/description keyword matching
    2. Known QWERTZ brand+model database
    3. EAN prefix (400-440 = German origin)
    4. Cross-market presence (DE + non-DE)
"""

# Keepa domain IDs per market
DOMAINS = {
    "DE": 3,
    "UK": 2,
    "FR": 4,
    "IT": 8,
    "ES": 9,
}

# Layer 1: Title keyword matching
LAYOUT_TITLE_KEYWORDS = {
    "qwertz": [
        "qwertz",
        "deutsch",
        "german layout",
        "iso-de",
        "german keyboard",
        "deutsche tastatur",
        "tastatur qwertz",
        "de layout",
        "germanisches layout",
        "deutsches layout",
    ],
    "azerty": [
        "azerty",
        "french layout",
        "clavier francais",
        "clavier azerty",
        "fr layout",
        "disposition francaise",
    ],
    "qwerty_uk": [
        "uk layout",
        "british layout",
        "qwerty uk",
        "english uk",
        "gb layout",
    ],
    "qwerty_it": [
        "italian layout",
        "italiano",
        "it layout",
        "tastiera italiana",
    ],
    "qwerty_es": [
        "spanish layout",
        "espanol",
        "es layout",
        "teclado espanol",
    ],
}

# Layer 2: Known QWERTZ brand+model combinations
KNOWN_QWERTZ_MODELS = [
    "cherry kc 1000",
    "cherry kc 6000",
    "cherry stream",
    "cherry g80",
    "cherry g84",
    "cherry g86",
    "cherry jk",
    "cherry mx board",
    "cherry b.unlimited",
    "cherry dw 9100",
    "cherry dw 5100",
    "logitech k120 de",
    "logitech k400 de",
    "logitech k380 de",
    "logitech mk270 de",
    "logitech mk295 de",
    "logitech mk545 de",
    "logitech g413 de",
    "logitech g512 de",
    "logitech g pro de",
    "perixx periboard",
    "microsoft sculpt de",
    "microsoft ergonomic de",
    "microsoft surface de",
    "corsair k70 de",
    "corsair k95 de",
    "corsair k65 de",
    "razer blackwidow de",
    "razer huntsman de",
    "razer ornata de",
    "steelseries apex de",
    "hyperx alloy de",
    "ducky one 2 de",
    "keychron k2 de",
    "keychron q1 de",
    "keychron k8 de",
    "fortez kb1",
    "hama kc-200",
    "hama kc-500",
    "logilink id0194",
    "trust ody",
    "medion akoya",
]

# Expected layout per market
EXPECTED_LAYOUT = {
    "DE": "qwertz",
    "UK": "qwerty_uk",
    "FR": "azerty",
    "IT": "qwerty_it",
    "ES": "qwerty_es",
}

# Fallback keyboard category IDs (Amazon browse nodes)
FALLBACK_KEYBOARD_CATEGORIES = {
    "DE": 340843031,   # Tastaturen
    "UK": 340831031,   # Keyboards
    "FR": 340843031,   # Claviers
    "IT": 340843031,   # Tastiere
    "ES": 340843031,   # Teclados
}


def detect_layout_text(title: str) -> tuple:
    """Layer 1: Detect layout from title keywords."""
    title_lower = title.lower()
    for layout, keywords in LAYOUT_TITLE_KEYWORDS.items():
        for kw in keywords:
            if kw in title_lower:
                return layout, "title_keyword"
    return None, ""


def detect_layout_brand_model(title: str, brand: str) -> tuple:
    """Layer 2: Detect layout from known QWERTZ brand+model combos."""
    combined = f"{brand} {title}".lower()
    for model in KNOWN_QWERTZ_MODELS:
        if model in combined:
            return "qwertz", "brand_model_db"
    return None, ""


def detect_layout_ean(ean: str) -> tuple:
    """Layer 3: EAN prefix 400-440 = German origin."""
    if not ean or len(ean) < 3:
        return None, ""
    prefix = ean[:3]
    try:
        prefix_int = int(prefix)
        if 400 <= prefix_int <= 440:
            return "qwertz", "ean_prefix"
    except ValueError:
        pass
    return None, ""


def detect_layout_cross_market(present_markets: set) -> tuple:
    """Layer 4: Cross-market presence (DE + non-DE market)."""
    if "DE" in present_markets and len(present_markets) > 1:
        non_de = present_markets - {"DE"}
        if non_de:
            return "qwertz", "cross_market"
    return None, ""


def detect_layout(entry: dict) -> dict:
    """Run multi-layer layout detection on a single entry.

    Args:
        entry: Product dict with keys like title, brand, ean,
               present_markets, title_DE, description, features, etc.

    Returns:
        Dict with detected_layout, detection_layer, confidence.
    """
    title = entry.get("title", "")
    brand = entry.get("brand", "")
    ean = entry.get("ean", "")
    present_markets = entry.get("present_markets", set())
    if isinstance(present_markets, str):
        present_markets = set(present_markets.split(",")) if present_markets else set()

    # Also check market-specific titles
    all_titles = [title]
    for market in DOMAINS:
        mt = entry.get(f"title_{market}", "")
        if mt and mt not in all_titles:
            all_titles.append(mt)

    combined_title = " ".join(all_titles)

    # Combine description + features from all markets
    description = entry.get("description", "")
    features = entry.get("features", "")
    for market in DOMAINS:
        desc_m = entry.get(f"description_{market}", "")
        feat_m = entry.get(f"features_{market}", "")
        if desc_m and desc_m not in description:
            description += " " + desc_m
        if feat_m and feat_m not in features:
            features += " " + feat_m

    # Layer 1 checks title + description + features
    combined_text = combined_title + " " + description + " " + features

    # Layer 1: Title/description/features keywords (high confidence)
    layout, layer = detect_layout_text(combined_text)
    if layout:
        return {"detected_layout": layout, "detection_layer": layer, "confidence": "high"}

    # Layer 2: Brand+Model DB (high confidence)
    layout, layer = detect_layout_brand_model(combined_title, brand)
    if layout:
        return {"detected_layout": layout, "detection_layer": layer, "confidence": "high"}

    # Layer 3: EAN prefix (medium confidence)
    layout, layer = detect_layout_ean(ean)
    if layout:
        return {"detected_layout": layout, "detection_layer": layer, "confidence": "medium"}

    # Layer 4: Cross-market presence (low confidence)
    layout, layer = detect_layout_cross_market(present_markets)
    if layout:
        return {"detected_layout": layout, "detection_layer": layer, "confidence": "low"}

    return {"detected_layout": "unknown", "detection_layer": "none", "confidence": "none"}


def classify_mismatch(detected_layout: str, market: str) -> tuple:
    """Determine if detected layout mismatches expected layout for market.

    Returns:
        (is_mismatch: bool, layout_label: str)
    """
    expected = EXPECTED_LAYOUT.get(market, "")
    if detected_layout == "unknown":
        return False, "unknown"
    if not expected:
        return False, "unknown"
    if detected_layout != expected:
        return True, detected_layout
    return False, detected_layout
