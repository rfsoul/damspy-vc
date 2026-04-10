#!/usr/bin/env python3
"""Serve the DAMSpy VC monitor page over HTTP so sibling JSON fetches work."""

from __future__ import annotations

import argparse
import sys
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve DAMSpy VC from the shared parent folder so the sibling DAMSpy-core JSON can be fetched."
    )
    parser.add_argument("--host", default="0.0.0.0", help="Host interface to bind. Default: 0.0.0.0")
    parser.add_argument("--port", type=int, default=8000, help="Port to listen on. Default: 8000")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not try to open the browser automatically.",
    )
    return parser.parse_args()


def get_paths() -> tuple[Path, Path, Path]:
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    shared_root = repo_root.parent
    index_path = repo_root / "src" / "index.html"
    return repo_root, shared_root, index_path


def format_url(host: str, port: int, page_path: str) -> str:
    browser_host = "localhost" if host in {"0.0.0.0", "::"} else host
    return f"http://{browser_host}:{port}{page_path}"


def log_environment(repo_root: Path, shared_root: Path, page_url: str) -> None:
    json_path = shared_root / "DAMspy-core" / "src" / "DAMspy_logs" / "latest_woym.json"

    print(f"Serving directory: {shared_root}")
    print(f"VC repository:      {repo_root}")
    print(f"Monitor page:       {page_url}")
    print(f"Expected JSON:      /DAMspy-core/src/DAMspy_logs/latest_woym.json")
    print(f"JSON file on disk:  {json_path}")

    if not json_path.exists():
        print("Warning: JSON file does not exist at startup. The page will show DATA UNAVAILABLE until it appears.")

    print("Press Ctrl+C to stop.")


def main() -> int:
    args = parse_args()
    repo_root, shared_root, index_path = get_paths()

    if not index_path.exists():
        print(f"Error: monitor page not found at {index_path}", file=sys.stderr)
        return 1

    repo_name = repo_root.name
    page_path = f"/{repo_name}/src/index.html"
    page_url = format_url(args.host, args.port, page_path)

    handler = partial(SimpleHTTPRequestHandler, directory=str(shared_root))

    try:
        with ThreadingHTTPServer((args.host, args.port), handler) as server:
            print()
            log_environment(repo_root, shared_root, page_url)
            print()

            if not args.no_browser:
                webbrowser.open(page_url)

            server.serve_forever()
    except OSError as exc:
        print(f"Error: could not start server on {args.host}:{args.port}: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nServer stopped.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
