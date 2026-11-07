from __future__ import annotations

from collections.abc import Callable
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from shuyi import application
from shuyi.contracts import ShuyiError

app = typer.Typer(
    name="shuyi",
    help="Restyle existing EPUB books without rewriting their prose or navigation.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)
console = Console()
errors = Console(stderr=True)


def _run(operation: Callable[[], dict[str, object]]) -> None:
    try:
        result = operation()
    except ShuyiError as error:
        errors.print(f"Error: {error}", markup=False)
        raise typer.Exit(2) from None
    except KeyboardInterrupt:
        errors.print("Interrupted; completed analysis checkpoints are retained.")
        raise typer.Exit(130) from None
    except Exception:
        errors.print("Error: operation_failed", markup=False)
        raise typer.Exit(2) from None
    console.print_json(data=result)


@app.command("style")
def style_command(
    source: Annotated[Path, typer.Argument(help="Original EPUB to restyle.")],
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    css: Annotated[Path | None, typer.Option(help="Custom restricted reading CSS.")] = None,
    reanalyze: Annotated[bool, typer.Option(help="Refresh cached semantic analysis.")] = False,
    config: Annotated[Path | None, typer.Option(help="Explicit TOML model configuration.")] = None,
) -> None:
    _run(
        lambda: application.style(
            source,
            output,
            css_path=css,
            config_path=config,
            reanalyze=reanalyze,
            on_progress=lambda message: errors.print(message, markup=False),
        )
    )


@app.command("inspect")
def inspect_command(
    source: Annotated[Path, typer.Argument(help="EPUB to inspect locally.")],
) -> None:
    _run(lambda: application.inspect_book(source))


@app.command("check")
def check_command(
    source: Annotated[Path, typer.Argument(help="EPUB to validate locally.")],
    epubcheck: Annotated[bool, typer.Option(help="Also run the installed EPUBCheck.")] = False,
) -> None:
    _run(lambda: application.check(source, epubcheck=epubcheck))


def _version(value: bool) -> None:
    if value:
        typer.echo(package_version("shuyi"))
        raise typer.Exit()


@app.callback()
def options(
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show version.")
    ] = False,
) -> None:
    pass


def main() -> None:
    app()
