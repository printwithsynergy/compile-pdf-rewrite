"""Click subcommand registration for ``compile-pdf rewrite``.

Local mode reads the input + plan from disk and runs the engine in-process.
HTTP mode (``COMPILE_API_BASE`` set) is wired in Phase 1.x once the
sidecar deploy lights up.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from compile_pdf_rewrite.engine import RewritePlanError, apply_plan
from compile_pdf_rewrite.plan_schema import RewritePlan, rewrite_plan_json_schema
from compile_pdf_rewrite.verify import verify_rewrite


def register(group: click.Group) -> None:
    """Attach the ``rewrite`` subcommand to the top-level CLI group."""

    @group.command("rewrite", help="Apply a rewrite plan to a PDF.")
    @click.option(
        "--plan",
        "plan_path",
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        required=True,
        help="JSON rewrite-plan document.",
    )
    @click.option(
        "--verify/--no-verify",
        default=True,
        help="Run three-layer post-condition checks (spec §2.3) before writing output.",
    )
    @click.argument(
        "input_path",
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
    )
    @click.argument(
        "output_path",
        type=click.Path(dir_okay=False, path_type=Path),
    )
    def rewrite_cmd(
        plan_path: Path,
        input_path: Path,
        output_path: Path,
        verify: bool,
    ) -> None:
        plan_dict = json.loads(plan_path.read_text(encoding="utf-8"))
        try:
            plan = RewritePlan.model_validate(plan_dict)
        except Exception as exc:
            click.echo(f"plan validation failed: {exc}", err=True)
            sys.exit(3)

        input_bytes = input_path.read_bytes()
        try:
            result = apply_plan(input_bytes, plan)
        except RewritePlanError as exc:
            click.echo(f"plan rejected: {exc}", err=True)
            sys.exit(4)

        if verify:
            check = verify_rewrite(
                input_bytes=input_bytes,
                output_bytes=result.output_bytes,
                plan=plan,
            )
            if not check.passed:
                click.echo("verify failed:", err=True)
                for failure in check.failures:
                    click.echo(f"  - {failure}", err=True)
                sys.exit(4)

        output_path.write_bytes(result.output_bytes)
        click.echo(
            json.dumps(
                {
                    "ops_applied": result.ops_applied,
                    "pdf_sha256": result.pdf_sha256,
                    "output": str(output_path),
                },
                indent=2,
            )
        )

    @group.command("rewrite-schema", hidden=True, help="Dump the rewrite-plan JSON Schema.")
    def rewrite_schema_cmd() -> None:
        click.echo(json.dumps(rewrite_plan_json_schema(), indent=2))
