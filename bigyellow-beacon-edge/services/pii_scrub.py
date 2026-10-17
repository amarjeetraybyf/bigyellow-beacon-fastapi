"""
Free-text PII scrubber and EXIF stripper for shipboard privacy.
Scrubs crew names, phone numbers, email addresses, and passport/seaman IDs -> [crew]
Strips EXIF GPS metadata from photos before queuing for shore.
"""

import re
import io
from typing import List, Optional

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
PHONE_REGEX = re.compile(r"(\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")
SEAMAN_BOOK_REGEX = re.compile(r"\b(SB|P|ID)[0-9]{6,10}\b", re.IGNORECASE)


def scrub_pii(text: str, roster_names: Optional[List[str]] = None) -> str:
    """
    Scrubs emails, phones, ID numbers, and specific crew roster names.
    Replaces with '[crew]'.
    """
    if not text:
        return text

    scrubbed = EMAIL_REGEX.sub("[crew]", text)
    scrubbed = PHONE_REGEX.sub("[crew]", scrubbed)
    scrubbed = SEAMAN_BOOK_REGEX.sub("[crew]", scrubbed)

    if roster_names:
        for name in roster_names:
            if name and len(name) > 2:
                pattern = re.compile(re.escape(name), re.IGNORECASE)
                scrubbed = pattern.sub("[crew]", scrubbed)

    return scrubbed


def strip_exif(image_bytes: bytes) -> bytes:
    """
    Strips EXIF and GPS geolocation metadata from JPEG/PNG images.
    Falls back gracefully if PIL is not installed or error occurs.
    """
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))
        fmt = (img.format or "JPEG").upper()
        if fmt in ("JPEG", "JPG") and img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        data = list(img.getdata())
        image_without_exif = Image.new(img.mode, img.size)
        image_without_exif.putdata(data)
        out = io.BytesIO()
        image_without_exif.save(out, format=fmt if fmt in ("JPEG", "PNG", "WEBP") else "JPEG")
        return out.getvalue()
    except Exception:
        return image_bytes
