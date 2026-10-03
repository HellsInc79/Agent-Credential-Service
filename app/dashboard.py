from fastapi.responses import HTMLResponse
from pathlib import Path


def render_dashboard():

    template_path = Path(__file__).resolve().parent.parent / "templates" / "dashboard.html"
    html = template_path.read_text(encoding="utf-8")

    return HTMLResponse(
        content=html
    )
