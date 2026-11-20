"""Qualification-only HTML route layered on the existing read-only API."""

from pathlib import Path

import uvicorn
from fastapi.responses import HTMLResponse, Response

from evdt.api import app


@app.get("/qualification/browser", response_class=HTMLResponse, include_in_schema=False)
def browser_check():
    return HTMLResponse(
        Path("/app/p0_browser.html").read_text(), headers={"Cache-Control": "no-store"}
    )


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
