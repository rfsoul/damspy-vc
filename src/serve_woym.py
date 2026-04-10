#!/usr/bin/env python3
"""Serve the DAMSpy VC monitor page over HTTP so sibling JSON fetches work."""

from __future__ import annotations

import argparse
import posixpath
import sys
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


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


class WOYMRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, repo_root: Path, shared_root: Path, **kwargs):
        self.repo_root = repo_root
        self.shared_root = shared_root
        self.repo_name = repo_root.name
        super().__init__(*args, **kwargs)

    def translate_path(self, path: str) -> str:
        clean_path = urlsplit(path).path
        clean_path = unquote(clean_path)

        if clean_path in {"", "/"}:
            return str(self.repo_root / "src" / "index.html")

        relative_path = self._normalise_relative_path(clean_path)

        if relative_path.parts and relative_path.parts[0] == "DAMspy-core":
            return str(self.shared_root / relative_path)

        if relative_path.parts and relative_path.parts[0] == self.repo_name:
            relative_path = Path(*relative_path.parts[1:])

        return str(self.repo_root / relative_path)

    @staticmethod
    def _normalise_relative_path(path: str) -> Path:
        normalised = posixpath.normpath(path)
        parts = [part for part in normalised.split("/") if part not in {"", ".", ".."}]
        return Path(*parts) if parts else Path()


def format_url(host: str, port: int, page_path: str) -> str:
    browser_host = "localhost" if host in {"0.0.0.0", "::"} else host
    return f"http://{browser_host}:{port}{page_path}"


def log_environment(repo_root: Path, shared_root: Path, page_url: str) -> None:
    json_path = shared_root / "DAMspy-core" / "src" / "DAMspy_logs" / "latest_woym.json"

    print(f"Serving directory: {shared_root}")
    print(f"VC repository:      {repo_root}")
    print(f"Monitor page:       {page_url}")
    print(f"Expected JSON URL:  /DAMspy-core/src/DAMspy_logs/latest_woym.json")
    print(f"JSON file on disk:  {json_path}")

    if not json_path.exists():
        print("Warning: JSON file does not exist at startup. The page will show DATA UNAVAILABLE until it appears.")

    print("Press Ctrl+C to stop.")


def build_port_candidates(requested_port: int) -> list[int]:
    fallback_ports = [8001, 8080, 8765, 8888, 9000]
    candidates = [requested_port]

    for port in fallback_ports:
        if port not in candidates:
            candidates.append(port)

    return candidates


def main() -> int:
    args = parse_args()
    repo_root, shared_root, index_path = get_paths()

    if not index_path.exists():
        print(f"Error: monitor page not found at {index_path}", file=sys.stderr)
        return 1

    handler = lambda *handler_args, **handler_kwargs: WOYMRequestHandler(
        *handler_args,
        repo_root=repo_root,
        shared_root=shared_root,
        **handler_kwargs,
    )

    last_error: OSError | None = None

    for port in build_port_candidates(args.port):
        page_url = format_url(args.host, port, "/")

        try:
            with ThreadingHTTPServer((args.host, port), handler) as server:
                print()

                if port != args.port:
                    print(f"Port {args.port} was unavailable. Using port {port} instead.")

                log_environment(repo_root, shared_root, page_url)
                print()

                if not args.no_browser:
                    webbrowser.open(page_url)

                server.serve_forever()
                return 0
        except OSError as exc:
            last_error = exc
            continue
        except KeyboardInterrupt:
            print("\nServer stopped.")
            return 0

    print(f"Error: could not start server on host {args.host}. Last error: {last_error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
