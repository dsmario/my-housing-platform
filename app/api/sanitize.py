"""Sanitizing (Doc Vault §2, §3): every file is rebuilt before it reaches the vault.

  JPEG, PNG   pixels drawn into a new file: no EXIF (GPS, camera, author), no
              text chunks, nothing hidden after the image. Orientation is
              applied first, so photos stay upright.
  HEIC        the same, saved as JPEG, which every browser can open.
  PDF         each page drawn as an image and put in a new PDF: no scripts,
              links, forms, attachments, layers or metadata. Text can't be
              selected afterwards; for ID and housing papers the picture is
              what matters.

Anything that can't be rebuilt within the limits is rejected.

Parsing untrusted files is the riskiest step, so it runs in a child process with
memory and CPU limits and no AWS settings in its environment. That is not full
isolation: the child shares the worker's container, so a successful exploit
could still reach the task role. A separate sanitizer task with no AWS
permissions is the stronger option (listed for later).

Limits (configuration, §9):
  VAULT_SANITIZE_MAX_PAGES     pages per PDF (default 30)
  VAULT_SANITIZE_MAX_PIXELS    per image or rendered page (default 50 million)
  VAULT_SANITIZE_DPI           PDF page resolution (default 150)
  VAULT_SANITIZE_SECONDS       per file (default 60)
  VAULT_SANITIZE_MEMORY_MB     for the child process (default 768)

Run as a child: python -I sanitize.py <content-type>, file on stdin. Writes the
new content type, a newline, then the new file to stdout.
"""

import io
import os
import resource
import subprocess
import sys

DEFAULTS = {
    "VAULT_SANITIZE_MAX_PAGES": "30",
    "VAULT_SANITIZE_MAX_PIXELS": "50000000",
    "VAULT_SANITIZE_DPI": "150",
    "VAULT_SANITIZE_SECONDS": "60",
    "VAULT_SANITIZE_MEMORY_MB": "768",
}


class Rejected(Exception):
    """The file couldn't be rebuilt; the reason is for the log, not the youth."""


def setting(name: str) -> int:
    return int(os.environ.get(name) or DEFAULTS[name])


# Parent side ------------------------------------------------------------------


def run_limited(cmd: list[str], data: bytes, seconds: int, memory_mb: int) -> subprocess.CompletedProcess:
    """Runs cmd with data on stdin, under CPU, memory and wall-clock limits, with
    only the settings it needs in its environment (no AWS credentials)."""

    def limits():
        resource.setrlimit(resource.RLIMIT_AS, (memory_mb * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_CPU, (seconds, seconds))

    env = {k: v for k, v in os.environ.items() if k.startswith("VAULT_SANITIZE_")}
    env["PATH"] = "/usr/local/bin:/usr/bin:/bin"
    try:
        return subprocess.run(
            cmd, input=data, capture_output=True, env=env, timeout=seconds + 5, preexec_fn=limits, check=False
        )
    except subprocess.TimeoutExpired:
        raise Rejected("took too long") from None


def sanitize(content_type: str, data: bytes) -> tuple[bytes, str]:
    """Returns (rebuilt file, its content type), or raises Rejected."""
    result = run_limited(
        [sys.executable, "-I", os.path.abspath(__file__), content_type],
        data,
        setting("VAULT_SANITIZE_SECONDS"),
        setting("VAULT_SANITIZE_MEMORY_MB"),
    )
    if result.returncode != 0:
        reason = result.stderr.decode(errors="replace").strip().splitlines()
        raise Rejected(reason[-1] if reason else f"exit {result.returncode}")
    new_type, _, body = result.stdout.partition(b"\n")
    if not body:
        raise Rejected("no output")
    return body, new_type.decode()


# Child side -------------------------------------------------------------------


def rebuild_image(im, max_pixels: int):
    """A new image with the same pixels and nothing else."""
    from PIL import Image, ImageOps

    if im.width * im.height > max_pixels:
        raise Rejected("image too large")
    im = ImageOps.exif_transpose(im)
    if im.mode not in ("RGB", "RGBA", "L", "LA"):
        im = im.convert("RGBA" if "transparency" in im.info or im.mode in ("P", "PA") else "RGB")
    return Image.frombytes(im.mode, im.size, im.tobytes())


def clean_image(data: bytes, content_type: str, max_pixels: int) -> tuple[bytes, str]:
    import warnings

    from PIL import Image

    if content_type == "image/heic":
        import pillow_heif

        pillow_heif.register_heif_opener()
    Image.MAX_IMAGE_PIXELS = max_pixels
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            im = Image.open(io.BytesIO(data))
            im.load()
        except (Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise Rejected("image too large") from None
    clean = rebuild_image(im, max_pixels)

    out = io.BytesIO()
    if content_type == "image/png":
        clean.save(out, "PNG", optimize=True)
        return out.getvalue(), "image/png"
    # JPEG, and HEIC saved as JPEG. JPEG has no transparency, so flatten onto white.
    if clean.mode in ("RGBA", "LA"):
        background = Image.new("RGB", clean.size, "white")
        background.paste(clean, mask=clean.getchannel("A"))
        clean = background
    clean.convert("RGB").save(out, "JPEG", quality=90)
    return out.getvalue(), "image/jpeg"


def clean_pdf(data: bytes, max_pages: int, max_pixels: int, dpi: int) -> bytes:
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(data)  # forms and scripts are never run: no init_forms()
    except pdfium.PdfiumError as e:
        raise Rejected(f"unreadable PDF ({e})") from None
    if len(pdf) == 0:
        raise Rejected("no pages")
    if len(pdf) > max_pages:
        raise Rejected("too many pages")

    pages = []
    for page in pdf:
        width, height = page.get_size()  # points, 1/72 inch
        scale = dpi / 72
        if width * height * scale * scale > max_pixels:  # posters and oddly large pages
            scale = (max_pixels / (width * height)) ** 0.5
        pages.append(page.render(scale=scale).to_pil().convert("RGB"))
        page.close()
    pdf.close()

    out = io.BytesIO()
    pages[0].save(out, "PDF", save_all=True, append_images=pages[1:], resolution=dpi, quality=85)
    return out.getvalue()


def child_main(content_type: str) -> int:
    data = sys.stdin.buffer.read()
    try:
        if content_type == "application/pdf":
            body, new_type = clean_pdf(
                data, setting("VAULT_SANITIZE_MAX_PAGES"), setting("VAULT_SANITIZE_MAX_PIXELS"), setting("VAULT_SANITIZE_DPI")
            ), "application/pdf"
        elif content_type in ("image/jpeg", "image/png", "image/heic"):
            body, new_type = clean_image(data, content_type, setting("VAULT_SANITIZE_MAX_PIXELS"))
        else:
            raise Rejected(f"no sanitizer for {content_type}")
    except Rejected as e:
        print(str(e), file=sys.stderr)
        return 2
    except MemoryError:
        print("too much memory", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 - any parser failure means the file is rejected
        print(f"unreadable ({type(e).__name__})", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(new_type.encode() + b"\n" + body)
    return 0


if __name__ == "__main__":
    sys.exit(child_main(sys.argv[1]))
