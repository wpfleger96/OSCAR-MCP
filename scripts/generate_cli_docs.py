#!/usr/bin/env python3
"""Generate CLI reference documentation from Click help text."""

import subprocess

from pathlib import Path

COMMANDS = [
    "snore --help",
    "snore profile --help",
    "snore profile list --help",
    "snore profile show --help",
    "snore profile create --help",
    "snore profile delete --help",
    "snore profile set-default --help",
    "snore profile unset-default --help",
    "snore session --help",
    "snore session list --help",
    "snore session show --help",
    "snore session delete --help",
    "snore analysis --help",
    "snore analysis run --help",
    "snore analysis list --help",
    "snore analysis show --help",
    "snore analysis delete --help",
    "snore db --help",
    "snore db init --help",
    "snore db stats --help",
    "snore db vacuum --help",
    "snore db drop --help",
    "snore config --help",
    "snore config show --help",
    "snore event --help",
    "snore event export --help",
    "snore waveform --help",
    "snore waveform show --help",
    "snore waveform compare --help",
    "snore logs --help",
    "snore logs path --help",
    "snore logs show --help",
    "snore logs clear --help",
    "snore completions --help",
    "snore completions bash --help",
    "snore completions zsh --help",
    "snore completions install --help",
    "snore completions uninstall --help",
    "snore import --help",
    "snore validate --help",
    "snore setup --help",
    "snore upgrade --help",
]


def generate() -> None:
    """Generate CLI reference documentation from help text."""
    output = [
        "# SNORE CLI Reference\n\n",
        "Auto-generated from `--help`. Do not edit manually.\n\n",
        "This is the complete CLI reference for SNORE. For quick start examples and usage guides, see [README.md](../README.md).\n\n",
    ]

    for cmd in COMMANDS:
        result = subprocess.run(
            f"uv run {cmd}", shell=True, capture_output=True, text=True, check=False
        )

        if result.returncode != 0:
            print(f"Warning: Command '{cmd}' failed with exit code {result.returncode}")
            continue

        cmd_name = cmd.replace("snore ", "snore ").replace(" --help", "")
        output.append(f"## `{cmd_name}`\n\n")
        output.append("```\n")
        output.append(result.stdout)
        output.append("```\n\n")

    docs_dir = Path("docs")
    docs_dir.mkdir(exist_ok=True)

    output_file = docs_dir / "CLI_REFERENCE.md"
    output_file.write_text("".join(output))
    print(f"✓ Generated {output_file}")


if __name__ == "__main__":
    generate()
