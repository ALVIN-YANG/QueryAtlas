"""Read-only original document previews with stable HTML paragraph links."""

from html import escape

from fastapi.responses import HTMLResponse

from .extract import html_elements


def html_preview(path, filename):
    paragraphs = []
    for i, (tag, text) in enumerate(html_elements(path), 1):
        element = tag if tag in {"h1", "h2", "h3", "h4"} else "p"
        paragraphs.append(f'<{element} id="element-{i}">{escape(text)}</{element}>')
    return HTMLResponse(
        '<!doctype html><html lang="zh-CN"><meta charset="UTF-8">'
        f'<title>{escape(filename)}</title>'
        '<style>body{font:16px/1.7 system-ui,sans-serif;margin:3rem auto;max-width:48rem;'
        'padding:0 1.25rem}:target{background:#e5f4f0;outline:4px solid #e5f4f0}</style>'
        '<main>' + ''.join(paragraphs) + '</main></html>',
        headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"},
    )
