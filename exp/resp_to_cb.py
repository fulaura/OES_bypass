#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _detect_clipboard_command() -> list[str]:
	"""Return a command argv that reads clipboard content from stdin.

	Preference:
	- Wayland: wl-copy (wl-clipboard)
	- X11: xclip
	- X11: xsel
	"""
	if shutil.which("wl-copy"):
		return ["wl-copy"]

	if shutil.which("xclip"):
		# xclip reads stdin by default when -in is used.
		return ["xclip", "-selection", "clipboard", "-in"]

	if shutil.which("xsel"):
		return ["xsel", "--clipboard", "--input"]

	raise RuntimeError(
		"No clipboard tool found. Install one of: wl-clipboard (wl-copy), xclip, or xsel."
	)


def copy_to_clipboard(text: str) -> None:
	cmd = _detect_clipboard_command()

	# Ensure a stable encoding; clipboard tools expect bytes on stdin.
	data = text.encode("utf-8")

	# Under sudo, DISPLAY/WAYLAND vars can be stripped. In that case, prefer running
	# without sudo or preserve env with sudo -E.
	env = os.environ.copy()

	subprocess.run(cmd, input=data, check=True, env=env)


def _read_text_from_args(args: argparse.Namespace) -> str:
	if args.text is not None:
		return args.text

	if args.file is not None:
		return Path(args.file).read_text(encoding="utf-8")

	# Default: stdin. If stdin is a TTY and no --text/--file provided, be explicit.
	if sys.stdin.isatty():
		raise RuntimeError("No input provided. Use --text, --file, or pipe via stdin.")
	return sys.stdin.read()


def main(argv: list[str] | None = None) -> int:
	p = argparse.ArgumentParser(description="Copy provided text to clipboard (Wayland/X11).")
	p.add_argument("--text", default=None, help="Text to copy.")
	p.add_argument("--file", default=None, help="Read text from a file and copy.")
	p.add_argument(
		"--strip",
		action="store_true",
		help="Strip leading/trailing whitespace before copying.",
	)
	p.add_argument(
		"--no-trailing-newline",
		action="store_true",
		help="Remove a single trailing newline (common when piping).",
	)

	args = p.parse_args(argv)

	try:
		text = _read_text_from_args(args)
		if args.no_trailing_newline and text.endswith("\n"):
			text = text[:-1]
		if args.strip:
			text = text.strip()

		copy_to_clipboard(text)
		return 0
	except Exception as e:
		print(f"clipboard: {e}", file=sys.stderr)
		return 1


if __name__ == "__main__":
	raise SystemExit(main())

