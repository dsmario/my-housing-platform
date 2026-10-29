"""Test files built in code: real PDFs and images, some carrying things cleaning must remove."""

import io

import pypdfium2 as pdfium
from PIL import Image


def jpeg_with_secrets(size=(64, 48), orientation=None) -> bytes:
    im = Image.new("RGB", size, (200, 30, 30))
    exif = Image.Exif()
    exif[0x010E] = "secret description"  # ImageDescription
    exif[0x013B] = "Secret Artist"  # Artist
    exif.get_ifd(0x8825)[2] = (37.0, 46.0, 30.0)  # GPS latitude
    if orientation:
        exif[0x0112] = orientation
    out = io.BytesIO()
    im.save(out, "JPEG", exif=exif.tobytes())
    return out.getvalue() + b"<script>hidden after the image</script>"


def pdf_with_script() -> bytes:
    """One page saying Hello, with a script that runs on open, an author and an attachment."""
    return b"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R /OpenAction 4 0 R /Names << /EmbeddedFiles << /Names [(x.exe) 8 0 R] >> >> >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 5 0 R /Resources << /Font << /F1 6 0 R >> >> >> endobj
4 0 obj << /S /JavaScript /JS (app.alert('evil')) >> endobj
5 0 obj << /Length 37 >> stream
BT /F1 24 Tf 20 100 Td (Hello) Tj ET
endstream endobj
6 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj
7 0 obj << /Author (Secret Author) /Title (Secret Title) >> endobj
8 0 obj << /Type /Filespec /F (x.exe) /EF << /F 9 0 R >> >> endobj
9 0 obj << /Type /EmbeddedFile /Length 12 >> stream
MZ-not-a-exe
endstream endobj
trailer << /Root 1 0 R /Info 7 0 R >>
%%EOF
"""


def pdf_pages(n: int = 1) -> bytes:
    pages = [Image.new("RGB", (300, 400), (i * 60, 100, 100)) for i in range(n)]
    out = io.BytesIO()
    pages[0].save(out, "PDF", save_all=True, append_images=pages[1:])
    return out.getvalue()


def page_count(data: bytes) -> int:
    return len(pdfium.PdfDocument(data))
