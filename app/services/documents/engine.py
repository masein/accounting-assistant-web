"""HTML/CSS → PDF rendering via WeasyPrint, plus the Jinja2 environment.

One generic template (`document.html`) renders every document type from a rich
context built per-document in `render.py`."""
from __future__ import annotations

import functools
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


@functools.lru_cache(maxsize=1)
def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_html(context: dict, template_name: str = "document.html") -> str:
    return _env().get_template(template_name).render(**context)


def html_to_pdf(html: str) -> bytes:
    from weasyprint import HTML  # imported lazily — needs pango/cairo system libs
    return HTML(string=html, base_url=str(TEMPLATES_DIR)).write_pdf()


def render_pdf(context: dict, template_name: str = "document.html") -> bytes:
    return html_to_pdf(render_html(context, template_name))
