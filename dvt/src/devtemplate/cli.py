from __future__ import annotations

import typer

app = typer.Typer(help="dvt: dev-style named devcontainer templates on top of DevPod.")


@app.callback(invoke_without_command=True)
def callback() -> None:
    """dvt: dev-style named devcontainer templates on top of DevPod."""
    pass


def main() -> None:
    app()


if __name__ == "__main__":
    main()
