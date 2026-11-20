"""Serve an already prepared static demo locally; never starts SUMO or services."""

import argparse
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from current_demo import private_path, resolve_current, site_inventory

ROOT = Path(__file__).resolve().parents[2]


def resolve_site(site=None, *, root=ROOT):
    selected = private_path(root, site) if site is not None else resolve_current(root=root)
    site_inventory(selected)
    if not (selected / "cases/montreal/manifest.json").is_file():
        raise ValueError("Serve a prepared browser-demo site, not a project or evidence root")
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--site",
        type=Path,
        help="Reviewed private site directory; defaults to the selected current demo",
    )
    parser.add_argument("--port", type=int, default=18770)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    try:
        site = resolve_site(args.site, root=ROOT)
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    if not 1024 <= args.port <= 65535:
        parser.error("Use a local unprivileged port")
    with ThreadingHTTPServer(
        ("127.0.0.1", args.port), partial(SimpleHTTPRequestHandler, directory=str(site))
    ) as server:
        url = f"http://127.0.0.1:{args.port}/demo.html"
        print(
            "Recorded demo:",
            url,
            "\nCtrl+C stops this static viewer. It does not affect a vehicle.",
            flush=True,
        )
        if not args.no_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
