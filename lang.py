"""
Indian language translation for the personal lawyer.
All 22 scheduled languages + English.
Primary:  Google Translate (gtx endpoint, keyless, best quality)
Fallback: MyMemory (keyless, 50k chars/day with email param)
Optional: Bhashini (free gov key — wire if BHASHINI_API_KEY in .env)
"""
import urllib.request
import urllib.parse
import json
import os

# ---------------------------------------------------------------------------
# Language registry
# name → (iso_code, google_code, mymemory_pair, display_name, native_name)
# ---------------------------------------------------------------------------
LANGUAGES = {
    "hindi":     ("hi",  "hi",  "hi|en",  "Hindi",     "हिन्दी"),
    "bengali":   ("bn",  "bn",  "bn|en",  "Bengali",   "বাংলা"),
    "telugu":    ("te",  "te",  "te|en",  "Telugu",    "తెలుగు"),
    "marathi":   ("mr",  "mr",  "mr|en",  "Marathi",   "मराठी"),
    "tamil":     ("ta",  "ta",  "ta|en",  "Tamil",     "தமிழ்"),
    "urdu":      ("ur",  "ur",  "ur|en",  "Urdu",      "اردو"),
    "gujarati":  ("gu",  "gu",  "gu|en",  "Gujarati",  "ગુજરાતી"),
    "kannada":   ("kn",  "kn",  "kn|en",  "Kannada",   "ಕನ್ನಡ"),
    "malayalam": ("ml",  "ml",  "ml|en",  "Malayalam", "മലയാളം"),
    "odia":      ("or",  "or",  "or|en",  "Odia",      "ଓଡ଼ିଆ"),
    "punjabi":   ("pa",  "pa",  "pa|en",  "Punjabi",   "ਪੰਜਾਬੀ"),
    "assamese":  ("as",  "as",  "as|en",  "Assamese",  "অসমীয়া"),
    "maithili":  ("mai", "mai", "mai|en", "Maithili",  "मैथिली"),
    "sanskrit":  ("sa",  "sa",  "sa|en",  "Sanskrit",  "संस्कृतम्"),
    "konkani":   ("kok", "gom", "kok|en", "Konkani",   "कोंकणी"),
    "manipuri":  ("mni", "mni", "mni|en", "Manipuri",  "মৈতৈলোন্"),
    "nepali":    ("ne",  "ne",  "ne|en",  "Nepali",    "नेपाली"),
    "sindhi":    ("sd",  "sd",  "sd|en",  "Sindhi",    "سنڌي"),
    "bodo":      ("brx", "brx", "brx|en", "Bodo",      "बड़ो"),
    "santali":   ("sat", "sat", "sat|en", "Santali",   "ᱥᱟᱱᱛᱟᱲᱤ"),
    "kashmiri":  ("ks",  "ks",  "ks|en",  "Kashmiri",  "کٲشُر"),
    "dogri":     ("doi", "doi", "doi|en", "Dogri",     "डोगरी"),
    "english":   ("en",  "en",  "en|en",  "English",   "English"),
}

# Reverse map: google code → language key
LANG_BY_CODE: dict[str, str] = {}
for _k, _v in LANGUAGES.items():
    LANG_BY_CODE[_v[1]] = _k          # google code
    LANG_BY_CODE[_v[0]] = _k          # iso code (may differ, e.g. "gom" vs "kok")

UA = "Mozilla/5.0 (compatible; IndianLawyerBot/1.0)"


# ---------------------------------------------------------------------------
# Internal: provider calls
# ---------------------------------------------------------------------------

def _google_translate(text: str, src: str, tgt: str) -> str:
    """Google unofficial gtx endpoint — keyless, highest quality."""
    url = (
        "https://translate.googleapis.com/translate_a/single"
        f"?client=gtx&sl={src}&tl={tgt}&dt=t&q={urllib.parse.quote(text)}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.load(r)
    return "".join(part[0] for part in d[0] if part[0])


def _mymemory_translate(text: str, langpair: str,
                         email: str = "lawyer@polymarket.bot") -> str:
    """MyMemory — keyless fallback, 50k chars/day with email."""
    url = (
        "https://api.mymemory.translated.net/get"
        f"?q={urllib.parse.quote(text[:500])}&langpair={langpair}&de={email}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.load(r)
    return d["responseData"]["translatedText"]


def _resolve_google_code(lang_key: str) -> str:
    """Return the google_code for a language key or a raw ISO/google code."""
    entry = LANGUAGES.get(lang_key.lower())
    if entry:
        return entry[1]  # google_code
    # Passed raw code — return as-is
    return lang_key


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def translate(text: str, src: str = "auto", tgt: str = "en") -> str:
    """
    Translate text between languages.

    src / tgt accept:
      - language key  e.g. "hindi", "tamil"
      - ISO/Google code  e.g. "hi", "ta"
      - "auto"  (src only) — Google auto-detects

    Returns translated string; fails soft (returns original on error).
    """
    if not text or not text.strip():
        return text

    src_code = "auto" if src == "auto" else _resolve_google_code(src)
    tgt_code = _resolve_google_code(tgt)

    if src_code == tgt_code and src_code != "auto":
        return text

    # Primary: Google
    try:
        return _google_translate(text, src_code, tgt_code)
    except Exception:
        pass

    # Fallback: MyMemory
    try:
        pair = f"{src_code}|{tgt_code}" if src_code != "auto" else f"en|{tgt_code}"
        return _mymemory_translate(text, pair)
    except Exception:
        pass

    return text  # final fail-soft


def detect(text: str) -> str:
    """
    Detect the language of text.
    Returns a language key e.g. "hindi", "tamil", or "english".
    """
    try:
        url = (
            "https://translate.googleapis.com/translate_a/single"
            f"?client=gtx&sl=auto&tl=en&dt=t&dt=ld"
            f"&q={urllib.parse.quote(text[:200])}"
        )
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.load(r)
        detected_code = d[2] if len(d) > 2 else "en"
        return LANG_BY_CODE.get(detected_code, "english")
    except Exception:
        return "english"


def to_english(text: str, src_lang: str | None = None) -> tuple[str, str]:
    """
    Translate text to English.
    Auto-detects source language if src_lang is not given.

    Returns (translated_text, detected_language_key).
    """
    if not src_lang:
        src_lang = detect(text)
    if src_lang == "english":
        return text, "english"
    return translate(text, src=src_lang, tgt="en"), src_lang


def from_english(text: str, tgt_lang: str) -> str:
    """
    Translate from English to target language.
    Returns original text if tgt_lang is "english" or on error.
    """
    if tgt_lang == "english" or not tgt_lang:
        return text
    return translate(text, src="en", tgt=tgt_lang)


def language_label(key: str) -> str:
    """Return 'नेपाली (Nepali)' style label for a language key."""
    entry = LANGUAGES.get(key.lower())
    if not entry:
        return key.title()
    native, display = entry[4], entry[3]
    if native == display:
        return display
    return f"{native} ({display})"


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        ("मुझे गिरफ्तार कर लिया गया है, मेरे अधिकार क्या हैं?", None),
        ("என்னை கைது செய்தால் என் உரிமைகள் என்ன?", None),
        ("నన్ను అరెస్టు చేస్తే నా హక్కులు ఏమిటి?", None),
        ("আমাকে গ্রেফতার করলে আমার অধিকার কী?", None),
    ]
    REPLY_EN = "You have the right to remain silent and consult a lawyer."
    print("=" * 65)
    print("lang.py self-test — 4 Indian language round-trips")
    print("=" * 65)
    for q, lang in tests:
        detected = detect(q)
        english, _ = to_english(q)
        back = from_english(REPLY_EN, detected)
        print(f"\n[{detected}]")
        print(f"  IN : {q[:60]}")
        print(f"  EN : {english[:70]}")
        print(f"  OUT: {back[:90]}")
    print("\nAll scheduled languages:")
    for key, entry in LANGUAGES.items():
        print(f"  {key:<12} {entry[4]:<18} {entry[3]}")
