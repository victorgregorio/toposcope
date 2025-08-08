from __future__ import annotations

import http.server
import json
import os
import platform
import shutil
import socketserver
import sys
import tempfile
import webbrowser
from pathlib import Path
from typing import Any, Dict

import typer
from rich import print

from .model import Graph
from .collect.linux import collect_linux_hardware_graph, generate_demo_graph


app = typer.Typer(add_completion=False, no_args_is_help=True, help="TopoScope CLI")


def _ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


@app.command()
def scan(
    out: Path = typer.Option(Path("graph.json"), help="Output path for the hardware graph JSON"),
    demo: bool = typer.Option(
        False, "--demo", help="Generate a demo graph instead of scanning the host"
    ),
) -> None:
    """Scan the system (Linux) and write a normalized graph JSON."""

    if demo:
        graph: Graph = generate_demo_graph()
        print("[yellow]Generated demo graph[/yellow]")
    else:
        if platform.system() != "Linux":
            print("[red]Non-Linux OS detected. Use --demo to generate a sample graph.[/red]")
            raise typer.Exit(code=2)
        graph = collect_linux_hardware_graph()

    _ensure_parent_dir(out)
    with out.open("w", encoding="utf-8") as f:
        json.dump(graph, f, indent=2)
    print(f"[green]Wrote graph to[/green] {out}")


@app.command()
def serve(
    graph: Path = typer.Option(
        Path("graph.json"), help="Path to a hardware graph JSON produced by 'toposcope scan'"
    ),
    port: int = typer.Option(8080, help="Port for the local viewer web server"),
    open_browser: bool = typer.Option(True, help="Open browser after server starts"),
) -> None:
    """Serve a simple viewer for a given graph JSON using a local HTTP server."""
    viewer_dir = Path(__file__).resolve().parent.parent.parent / "viewer"
    if not viewer_dir.exists():
        print(f"[red]Viewer assets not found at {viewer_dir}[/red]")
        raise typer.Exit(code=1)

    if not graph.exists():
        print(f"[red]Graph JSON not found:[/red] {graph}")
        raise typer.Exit(code=2)

    # Create a temporary directory containing viewer + graph.json
    with tempfile.TemporaryDirectory(prefix="toposcope-view-") as tmpdir:
        tmp_path = Path(tmpdir)
        for item in viewer_dir.iterdir():
            dest = tmp_path / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)
        shutil.copy2(graph, tmp_path / "graph.json")

        os.chdir(tmp_path)
        handler = http.server.SimpleHTTPRequestHandler
        # Reusable server to avoid TIME_WAIT issues after Ctrl-C
        class ReusableTCPServer(socketserver.TCPServer):
            allow_reuse_address = True

        try:
            httpd = ReusableTCPServer(("127.0.0.1", port), handler)
        except OSError as ex:
            print(f"[red]Failed to start server on port {port}: {ex}[/red]")
            raise typer.Exit(code=3)

        try:
            actual_port = httpd.server_address[1]
            url = f"http://127.0.0.1:{actual_port}/index.html"
            print(f"[green]Serving viewer at[/green] {url}")
            if open_browser:
                try:
                    webbrowser.open_new_tab(url)
                except Exception:
                    pass
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[cyan]Shutting down viewer server[/cyan]")
        finally:
            try:
                httpd.shutdown()
            except Exception:
                pass
            httpd.server_close()


if __name__ == "__main__":
    app()
