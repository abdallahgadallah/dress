from io import BytesIO
from pathlib import Path

from django.core.files.base import ContentFile
from PIL import Image, ImageOps


HEIF_EXTENSIONS = {".heic", ".heif"}


def convert_heif_to_jpeg(upload):
    """Convert iPhone images to a browser-compatible JPEG before storage."""
    if not upload or Path(upload.name).suffix.lower() not in HEIF_EXTENSIONS:
        return upload

    image = Image.open(upload)
    image = ImageOps.exif_transpose(image)
    if image.mode in {"RGBA", "LA"}:
        background = Image.new("RGB", image.size, "white")
        background.paste(image, mask=image.getchannel("A"))
        image = background
    else:
        image = image.convert("RGB")

    output = BytesIO()
    image.save(output, format="JPEG", quality=90, optimize=True)
    return ContentFile(output.getvalue(), name=f"{Path(upload.name).stem}.jpg")
