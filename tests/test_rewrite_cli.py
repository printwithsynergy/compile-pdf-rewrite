"""CLI tests for ``compile-pdf rewrite``."""

from __future__ import annotations

import json
from pathlib import Path

import pikepdf
from click.testing import CliRunner

from compile_pdf.cli import cli


def test_rewrite_cli_round_trips(tmp_path: Path, simple_pdf: bytes) -> None:
    in_path = tmp_path / "in.pdf"
    out_path = tmp_path / "out.pdf"
    plan_path = tmp_path / "plan.json"
    in_path.write_bytes(simple_pdf)
    plan_path.write_text(
        json.dumps(
            {
                "ops": [
                    {"op": "metadata_set", "key": "Title", "value": "via cli"},
                    {"op": "page_rotate", "at_index": 0, "degrees": 90},
                ]
            }
        )
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["rewrite", "--plan", str(plan_path), str(in_path), str(out_path)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ops_applied"] == 2
    assert payload["pdf_sha256"]

    pdf = pikepdf.open(out_path)
    try:
        assert str(pdf.docinfo[pikepdf.Name.Title]) == "via cli"
        assert int(pdf.pages[0].obj.get(pikepdf.Name.Rotate)) == 90
    finally:
        pdf.close()


def test_rewrite_cli_rejects_invalid_plan(tmp_path: Path, simple_pdf: bytes) -> None:
    in_path = tmp_path / "in.pdf"
    out_path = tmp_path / "out.pdf"
    plan_path = tmp_path / "plan.json"
    in_path.write_bytes(simple_pdf)
    plan_path.write_text(json.dumps({"ops": [{"op": "wat"}]}))

    runner = CliRunner()
    result = runner.invoke(cli, ["rewrite", "--plan", str(plan_path), str(in_path), str(out_path)])
    assert result.exit_code == 3


def test_rewrite_cli_rejects_unapplicable_plan(tmp_path: Path, simple_pdf: bytes) -> None:
    in_path = tmp_path / "in.pdf"
    out_path = tmp_path / "out.pdf"
    plan_path = tmp_path / "plan.json"
    in_path.write_bytes(simple_pdf)
    plan_path.write_text(json.dumps({"ops": [{"op": "page_delete", "at_index": 99}]}))

    runner = CliRunner()
    result = runner.invoke(cli, ["rewrite", "--plan", str(plan_path), str(in_path), str(out_path)])
    assert result.exit_code == 4


def test_rewrite_schema_dumps_json_schema() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["rewrite-schema"])
    assert result.exit_code == 0
    schema = json.loads(result.output)
    assert "discriminator" in json.dumps(schema)


def test_top_level_help_lists_rewrite() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "rewrite" in result.output
