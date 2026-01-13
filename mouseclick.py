#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any

from mousemovement import create_uinput_mouse, move_cursor_uinput


@dataclass(frozen=True)
class BBox:
	"""Bounding box in absolute screen coordinates.

	Matches the OCR output format in this repo: (x, y, w, h).
	"""

	x: int
	y: int
	w: int
	h: int

	@staticmethod
	def from_any(value: object) -> "BBox":
		"""Parse bbox from common representations.

		Accepted inputs:
		- (x, y, w, h) list/tuple
		- "x,y,w,h" string
		- JSON list/tuple string: "[x,y,w,h]"
		"""
		if isinstance(value, BBox):
			return value
		if isinstance(value, (list, tuple)) and len(value) == 4:
			x, y, w, h = value
			return BBox(int(x), int(y), int(w), int(h))
		if isinstance(value, str):
			s = value.strip()
			if s.startswith("[") or s.startswith("("):
				try:
					parsed = json.loads(s.replace("(", "[").replace(")", "]"))
					return BBox.from_any(parsed)
				except Exception:
					pass
			parts = [p.strip() for p in s.split(",") if p.strip()]
			if len(parts) == 4:
				return BBox(int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]))
		raise ValueError(f"Unsupported bbox format: {value!r}")

	def clamp_point(self, x: int, y: int) -> tuple[int, int]:
		min_x = self.x
		min_y = self.y
		max_x = self.x + max(self.w - 1, 0)
		max_y = self.y + max(self.h - 1, 0)
		return (max(min_x, min(x, max_x)), max(min_y, min(y, max_y)))

	def normalized(self) -> "BBox":
		# Ensure w/h are non-negative.
		w = max(int(self.w), 0)
		h = max(int(self.h), 0)
		return BBox(int(self.x), int(self.y), w, h)

	def is_empty(self) -> bool:
		b = self.normalized()
		return b.w <= 0 or b.h <= 0


def pick_point_in_bbox(
	bbox: BBox,
	*,
	rule: str = "random",
	margin: int = 2,
	rng: random.Random | None = None,
) -> tuple[int, int]:
	"""Return a click point within bbox based on a rule.

	Rules (current):
	- "random": random point inside bbox
	- "left-middle": middle of the left side (x near left edge, y centered)

	`margin` keeps the point away from edges to avoid misclicking borders.
	"""
	b = bbox.normalized()
	if b.is_empty():
		raise ValueError(f"bbox is empty: {bbox}")

	margin = max(int(margin), 0)

	inner_left = b.x + min(margin, max(b.w - 1, 0))
	inner_top = b.y + min(margin, max(b.h - 1, 0))
	inner_right = b.x + max(b.w - 1 - margin, 0)
	inner_bottom = b.y + max(b.h - 1 - margin, 0)

	if inner_right < inner_left:
		inner_left = inner_right = b.x + max(b.w // 2, 0)
	if inner_bottom < inner_top:
		inner_top = inner_bottom = b.y + max(b.h // 2, 0)

	rule = (rule or "").strip().lower().replace("_", "-")
	if rule in ("random", "rand"):
		r = rng or random.Random()
		x = r.randint(inner_left, inner_right)
		y = r.randint(inner_top, inner_bottom)
		return b.clamp_point(x, y)

	if rule in ("left-middle", "leftmid", "left-mid", "left-middle-side"):
		x = inner_left
		y = b.y + max(b.h // 2, 0)
		return b.clamp_point(x, y)

	raise ValueError(f"Unknown rule: {rule!r} (expected 'random' or 'left-middle')")


def _require_ydotool() -> str:
	path = shutil.which("ydotool")
	if not path:
		raise RuntimeError(
			"ydotool not found in PATH. Install it (Arch: `sudo pacman -S ydotool`) "
			"and ensure ydotoold is running."
		)
	return path


def _detect_ydotool_socket() -> str | None:
	"""Best-effort detection of the ydotool daemon socket.

	Why this exists:
	- When you run via sudo, env vars like YDOTOOL_SOCKET are often stripped.
	- ydotool/ydotoold setups vary (some use /tmp/.ydotool_socket, others /run/user/<uid>/...).
	"""
	explicit = os.environ.get("YDOTOOL_SOCKET")
	if explicit and os.path.exists(explicit):
		return explicit

	common = [
		"/tmp/.ydotool_socket",
	]
	for p in common:
		if os.path.exists(p):
			return p

	# Try user runtime dir(s)
	uid_candidates: list[int] = []
	for key in ("SUDO_UID",):
		v = os.environ.get(key)
		if v and v.isdigit():
			uid_candidates.append(int(v))
	uid_candidates.append(os.getuid())

	for uid in uid_candidates:
		rundir = f"/run/user/{uid}"
		if not os.path.isdir(rundir):
			continue
		for name in (".ydotool_socket", "ydotool.socket"):
			p = os.path.join(rundir, name)
			if os.path.exists(p):
				return p

	return None


def uinput_move_and_click(
	*,
	x: int,
	y: int,
	button: str = "left",
	move_duration: float = 0.10,
	move_steps: int = 12,
	debug: bool = False,
) -> None:
	"""Move cursor (relative) and click via /dev/uinput using evdev.

	This avoids ydotool entirely.
	Requires:
	- `python-evdev`
	- permissions for /dev/uinput (often root)
	- Hyprland (we use hyprctl to get current cursor position)
	"""
	try:
		from evdev import UInput, ecodes  # type: ignore
	except Exception as e:
		raise RuntimeError("evdev is required for uinput backend (pip install evdev)") from e

	button = (button or "left").strip().lower()
	btn_code = {
		"left": ecodes.BTN_LEFT,
		"right": ecodes.BTN_RIGHT,
		"middle": ecodes.BTN_MIDDLE,
	}.get(button)
	if not btn_code:
		raise ValueError("button must be one of: left, right, middle")

	# Smooth movement handled by mousemovement.py (deterministic animation).
	with create_uinput_mouse(name="OES_bypass_uinput_mouse") as ui:
		move_cursor_uinput(x=x, y=y, ui=ui, duration=move_duration, steps=move_steps, debug=debug)
		# Small settle time before click
		time.sleep(0.01)
		ui.write(ecodes.EV_KEY, btn_code, 1)
		ui.syn()
		time.sleep(0.01)
		ui.write(ecodes.EV_KEY, btn_code, 0)
		ui.syn()


def ydotool_move_and_click(
	*,
	x: int,
	y: int,
	button: str = "left",
	debug: bool = False,
) -> None:
	"""Move cursor to absolute (x,y) and click.

	On Hyprland/Wayland, this relies on `ydotool` + `ydotoold`.
	"""
	_ = _require_ydotool()
	button = (button or "left").strip().lower()

	button_code = {
		"left": "0xC0",
		"right": "0xC1",
		"middle": "0xC2",
	}.get(button)
	if not button_code:
		raise ValueError("button must be one of: left, right, middle")

	move_cmd = ["ydotool", "mousemove", "-a", "-x", str(int(x)), "-y", str(int(y))]
	click_cmd = ["ydotool", "click", button_code]

	socket = _detect_ydotool_socket()
	env = os.environ.copy()
	if socket:
		env["YDOTOOL_SOCKET"] = socket

	if debug:
		if socket:
			print(f"YDOTOOL_SOCKET={socket}", flush=True)
		print(" ".join(move_cmd), flush=True)
		print(" ".join(click_cmd), flush=True)

	# NOTE: ydotool absolute movement accuracy depends on acceleration settings.
	try:
		subprocess.run(move_cmd, check=True, env=env, capture_output=True, text=True)
		subprocess.run(click_cmd, check=True, env=env, capture_output=True, text=True)
	except subprocess.CalledProcessError as e:
		stderr = (e.stderr or "").strip()
		stdout = (e.stdout or "").strip()
		extra = ""
		if stderr:
			extra += f"\nstderr: {stderr}"
		if stdout:
			extra += f"\nstdout: {stdout}"
		raise RuntimeError(
			"ydotool failed (is ydotoold running / socket accessible?). "
			f"Command: {e.cmd!r} Exit: {e.returncode}{extra}"
		) from e


def click_bbox(
	bbox: BBox | tuple[int, int, int, int] | list[int] | str,
	*,
	rule: str = "random",
	button: str = "left",
	margin: int = 2,
	seed: int | None = None,
	backend: str = "auto",
	move_duration: float = 0.10,
	move_steps: int = 12,
	dry_run: bool = False,
	debug: bool = False,
) -> tuple[int, int]:
	"""Pick a point inside `bbox` using `rule` and click it.

	Returns the chosen (x, y).
	"""
	bb = BBox.from_any(bbox)
	rng = random.Random(seed) if seed is not None else None
	x, y = pick_point_in_bbox(bb, rule=rule, margin=margin, rng=rng)
	if dry_run:
		if debug:
			print(f"dry-run: bbox={bb} rule={rule!r} -> ({x},{y})", flush=True)
		return (x, y)

	backend_norm = (backend or "auto").strip().lower()
	if backend_norm in ("auto", ""):
		# Prefer uinput (no ydotool), but fall back to ydotool if uinput/hyprctl fails.
		try:
			uinput_move_and_click(
				x=x,
				y=y,
				button=button,
				move_duration=move_duration,
				move_steps=move_steps,
				debug=debug,
			)
			return (x, y)
		except Exception as e:
			if debug:
				print(f"auto backend: uinput failed: {e}", flush=True)
			ydotool_move_and_click(x=x, y=y, button=button, debug=debug)
			return (x, y)

	if backend_norm in ("uinput", "evdev"):
		uinput_move_and_click(
			x=x,
			y=y,
			button=button,
			move_duration=move_duration,
			move_steps=move_steps,
			debug=debug,
		)
		return (x, y)

	if backend_norm in ("ydotool",):
		ydotool_move_and_click(x=x, y=y, button=button, debug=debug)
		return (x, y)

	raise ValueError("backend must be one of: auto, uinput, ydotool")
	return (x, y)


def _build_argparser() -> argparse.ArgumentParser:
	p = argparse.ArgumentParser(description="Click a region described by a bbox (x,y,w,h) using ydotool (Wayland friendly).")
	p.add_argument(
		"--bbox",
		required=True,
		help="Bounding box as 'x,y,w,h' or JSON '[x,y,w,h]'.",
	)
	p.add_argument(
		"--rule",
		default="random",
		help="Where to click inside bbox: random | left-middle",
	)
	p.add_argument(
		"--button",
		default="left",
		help="Mouse button: left | right | middle",
	)
	p.add_argument(
		"--margin",
		type=int,
		default=2,
		help="Pixels to stay away from bbox edges.",
	)
	p.add_argument(
		"--seed",
		type=int,
		default=None,
		help="Seed for random rule (repeatable clicks).",
	)
	p.add_argument(
		"--backend",
		default="auto",
		help="Click backend: auto | uinput | ydotool (auto prefers uinput)",
	)
	p.add_argument(
		"--move-duration",
		type=float,
		default=0.10,
		help="Seconds for cursor movement animation (uinput backend).",
	)
	p.add_argument(
		"--move-steps",
		type=int,
		default=12,
		help="Number of movement steps (uinput backend).",
	)
	p.add_argument(
		"--dry-run",
		action="store_true",
		help="Only print chosen coordinates; do not move/click.",
	)
	p.add_argument(
		"--debug",
		action="store_true",
		help="Print the ydotool commands used.",
	)
	return p


def main(argv: list[str] | None = None) -> int:
	args = _build_argparser().parse_args(argv)
	try:
		x, y = click_bbox(
			args.bbox,
			rule=args.rule,
			button=args.button,
			margin=args.margin,
			seed=args.seed,
			backend=args.backend,
			move_duration=args.move_duration,
			move_steps=args.move_steps,
			dry_run=args.dry_run,
			debug=args.debug,
		)
		if args.dry_run:
			print(f"{x},{y}")
		return 0
	except Exception as e:
		print(f"mouseclick: {e}")
		return 1


if __name__ == "__main__":
	raise SystemExit(main())




#when calling from other python file
# from mouseclick import click_bbox

# bbox = (100, 200, 300, 80)  # (x, y, w, h)

# # 1) random point inside bbox
# x, y = click_bbox(bbox, rule="random")

# # 2) middle of left side
# x, y = click_bbox(bbox, rule="left-middle")