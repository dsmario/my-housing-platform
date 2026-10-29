"""Sanitizing: rebuilt files keep the picture and lose everything else."""

import io
import sys
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from PIL import Image, PngImagePlugin
from samples import jpeg_with_secrets, page_count, pdf_pages, pdf_with_script

from sanitize import Rejected, run_limited, sanitize


# Images ------------------------------------------------------------------------


def test_jpeg_loses_exif_gps_and_hidden_data():
    out, kind = sanitize("image/jpeg", jpeg_with_secrets())
    assert kind == "image/jpeg"
    im = Image.open(io.BytesIO(out))
    assert im.size == (64, 48)
    assert dict(im.getexif()) == {}
    for secret in (b"secret description", b"Secret Artist", b"<script>"):
        assert secret not in out


def test_photo_stays_upright():
    out, _ = sanitize("image/jpeg", jpeg_with_secrets(size=(64, 48), orientation=6))  # taken sideways
    assert Image.open(io.BytesIO(out)).size == (48, 64)


def test_png_loses_text_chunks_and_keeps_transparency():
    im = Image.new("RGBA", (20, 10), (0, 0, 255, 128))
    info = PngImagePlugin.PngInfo()
    info.add_text("Author", "Secret Author")
    src = io.BytesIO()
    im.save(src, "PNG", pnginfo=info)
    out, kind = sanitize("image/png", src.getvalue())
    assert kind == "image/png" and b"Secret Author" not in out
    clean = Image.open(io.BytesIO(out))
    assert clean.mode == "RGBA" and clean.getpixel((0, 0)) == (0, 0, 255, 128)


def test_heic_becomes_jpeg():
    # A 32x32 green square, made with Debian's heif-enc (pillow-heif can read HEIC but not write it).
    heic = (Path(__file__).parent / "fixtures" / "tiny.heic").read_bytes()
    out, kind = sanitize("image/heic", heic)
    clean = Image.open(io.BytesIO(out))
    assert kind == "image/jpeg" and clean.format == "JPEG" and clean.size == (32, 32)
    r, g, b = clean.getpixel((16, 16))
    assert g > 150 and r < 80 and b < 80


def test_oversized_image_is_rejected(monkeypatch):
    monkeypatch.setenv("VAULT_SANITIZE_MAX_PIXELS", "10000")
    src = io.BytesIO()
    Image.new("1", (200, 200)).save(src, "PNG")  # 40,000 pixels, tiny on disk
    with pytest.raises(Rejected, match="too large"):
        sanitize("image/png", src.getvalue())


def test_damaged_image_is_rejected():
    with pytest.raises(Rejected, match="unreadable"):
        sanitize("image/jpeg", b"\xff\xd8\xff\xe0 not really a jpeg")


# PDFs -------------------------------------------------------------------------------


def test_pdf_is_redrawn_without_scripts_attachments_or_metadata():
    out, kind = sanitize("application/pdf", pdf_with_script())
    assert kind == "application/pdf" and out.startswith(b"%PDF")
    assert page_count(out) == 1
    for secret in (b"JavaScript", b"app.alert", b"OpenAction", b"EmbeddedFile", b"x.exe", b"Secret Author", b"Secret Title"):
        assert secret not in out


def test_redrawn_page_still_shows_the_content():
    out, _ = sanitize("application/pdf", pdf_with_script())
    page = pdfium.PdfDocument(out)[0].render(scale=1).to_pil().convert("L")
    assert page.getextrema()[0] < 100  # "Hello" left dark pixels on the white page


def test_pdf_keeps_its_pages():
    assert page_count(sanitize("application/pdf", pdf_pages(3))[0]) == 3


def test_pdf_with_too_many_pages_is_rejected(monkeypatch):
    monkeypatch.setenv("VAULT_SANITIZE_MAX_PAGES", "2")
    with pytest.raises(Rejected, match="too many pages"):
        sanitize("application/pdf", pdf_pages(3))


def test_damaged_pdf_is_rejected():
    with pytest.raises(Rejected, match="unreadable"):
        sanitize("application/pdf", b"%PDF-1.4\nthis is not a pdf at all")


def test_unknown_type_is_rejected():
    with pytest.raises(Rejected, match="no sanitizer"):
        sanitize("text/html", b"<html></html>")


# The child process ----------------------------------------------------------------------


def test_child_has_no_aws_credentials(monkeypatch):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-leak")
    monkeypatch.setenv("VAULT_SANITIZE_DPI", "150")
    r = run_limited([sys.executable, "-c", "import os; print(sorted(os.environ))"], b"", seconds=10, memory_mb=256)
    assert b"AWS_" not in r.stdout and b"VAULT_SANITIZE_DPI" in r.stdout


def test_slow_file_is_stopped():
    with pytest.raises(Rejected, match="too long"):
        run_limited([sys.executable, "-c", "import time; time.sleep(30)"], b"", seconds=1, memory_mb=256)


def test_memory_hungry_file_is_stopped():
    r = run_limited([sys.executable, "-c", "x = bytearray(1_000_000_000)"], b"", seconds=10, memory_mb=200)
    assert r.returncode != 0
