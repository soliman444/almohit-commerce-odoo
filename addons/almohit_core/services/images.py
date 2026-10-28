import base64
import binascii
import io
import re

from PIL import Image  # noqa: F401

from .api import error

PRODUCT_IMAGE_MAX_BYTES = 2 * 1024 * 1024
SHOP_IMAGE_MAX_BYTES = 4 * 1024 * 1024
IMAGE_MAX_WIDTH = 2000
IMAGE_MAX_HEIGHT = 2000

ALLOWED_IMAGE_TYPES = {
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/webp": (),
}

def _decode_image(
    payload,
    max_bytes=PRODUCT_IMAGE_MAX_BYTES,
    max_width=IMAGE_MAX_WIDTH,
    max_height=IMAGE_MAX_HEIGHT,
):
    raw = payload.get("image_base64")
    if raw in (None, ""):
        return None, None
    if not isinstance(raw, str):
        return None, error("INVALID_IMAGE_DATA", "Image data is invalid.", 400)
    content_type = (payload.get("image_content_type") or "").strip().lower()
    if content_type not in ALLOWED_IMAGE_TYPES:
        return None, error("INVALID_IMAGE_TYPE", "Image type is not allowed.", 400)
    cleaned = re.sub(r"^data:[^;]+;base64,", "", raw).replace("\n", "")
    try:
        data = base64.b64decode(cleaned, validate=True)
    except (binascii.Error, ValueError):
        return None, error("INVALID_IMAGE_DATA", "Image data is invalid.", 400)
    if not data:
        return None, error("INVALID_IMAGE_DATA", "Image data is invalid.", 400)
    if len(data) > max_bytes:
        return None, error("IMAGE_TOO_LARGE", "Image is too large.", 400)
    if content_type == "image/webp":
        if not (data.startswith(b"RIFF") and data[8:12] == b"WEBP"):
            return None, error("INVALID_IMAGE_DATA", "Image data is invalid.", 400)
    elif not any(data.startswith(sig) for sig in ALLOWED_IMAGE_TYPES[content_type]):
        return None, error("INVALID_IMAGE_DATA", "Image data is invalid.", 400)
    try:
        image = Image.open(io.BytesIO(data))
        width, height = image.size
    except Exception:
        return None, error("INVALID_IMAGE_DATA", "Image data is invalid.", 400)
    if width < 1 or height < 1:
        return None, error("INVALID_IMAGE_DATA", "Image data is invalid.", 400)
    if width > max_width or height > max_height:
        return None, error(
            "IMAGE_DIMENSIONS_TOO_LARGE",
            "Image dimensions are too large.",
            400,
        )
    try:
        image.load()
    except Exception:
        return None, error("INVALID_IMAGE_DATA", "Image data is invalid.", 400)
    return cleaned, None
