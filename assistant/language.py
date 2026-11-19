from __future__ import annotations

import re
from typing import Optional

_UZ_CYRILLIC = re.compile(r"[ўқғҳ]|\b(салом|рахмат|яхши|нима|қалесан|йўқ|бугун|эртага|керак|ассалому)\b", re.I)
_UZ_LATIN = re.compile(r"\b(salom|rahmat|yaxshi|nima|qalesan|qayerda|bugun|ertaga|kerak|xop|bopti|zor|hozir|keyin|men|sen|siz)\b", re.I)


def detect_language(text: str) -> Optional[str]:
    cyrillic = re.findall(r"[\u0400-\u04ff]", text)
    latin = re.findall(r"[A-Za-z]", text)
    if not cyrillic and not latin:
        return None
    if len(cyrillic) > len(latin):
        return "uz" if _UZ_CYRILLIC.search(text) else "ru"
    words = re.findall(r"[A-Za-z]+(?:['’ʻ`][A-Za-z]+)?", text.lower())
    if _UZ_LATIN.search(text):
        return "uz"
    for word in words:
        clean = re.sub(r"['’ʻ`].*", "", word)
        if clean in {"o", "g"} and len(word) > 1:
            return "uz"
        if clean.endswith(("misan", "misiz", "mizmi", "yapsan", "yapman", "yapti", "yapmiz", "ganman", "dingmi")):
            return "uz"
    return "en"
