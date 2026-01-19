#!/usr/bin/env python3
from __future__ import annotations

import argparse
import glob
import json
import os
import pwd
import random
import re
import select
import shutil
import subprocess
import sys
import termios
import time
import tty
from dataclasses import dataclass
from pathlib import Path
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


_CURSOR_RE = re.compile(r"(-?\d+)\s*,\s*(-?\d+)")


def _require_hyprctl() -> str:
	path = shutil.which("hyprctl")
	if not path:
		raise SystemExit(
			"hyprctl not found in PATH. This tool is intended for Hyprland on Wayland.\n"
			"Ensure Hyprland is installed and hyprctl is available, then retry."
		)
	return path


def _read_proc_environ(pid: int) -> dict[str, str]:
	try:
		data = Path(f"/proc/{pid}/environ").read_bytes()
	except Exception:
		return {}
	env: dict[str, str] = {}
	for entry in data.split(b"\0"):
		if not entry:
			continue
		try:
			k, v = entry.split(b"=", 1)
		except ValueError:
			continue
		try:
			env[k.decode()] = v.decode()
		except Exception:
			continue
	return env


def _guess_hyprland_env_from_sudo_user() -> dict[str, str] | None:
	"""Recover Hyprland env vars (esp. HYPRLAND_INSTANCE_SIGNATURE) under sudo."""
	if os.geteuid() != 0:
		return None
	user = os.environ.get("SUDO_USER")
	if not user:
		return None
	try:
		uid = pwd.getpwnam(user).pw_uid
	except KeyError:
		return None

	for proc_dir in Path("/proc").iterdir():
		if not proc_dir.name.isdigit():
			continue
		pid = int(proc_dir.name)
		try:
			status = (proc_dir / "status").read_text(errors="ignore")
		except Exception:
			continue
		m = re.search(r"^Uid:\s+(\d+)", status, flags=re.MULTILINE)
		if not m or int(m.group(1)) != uid:
			continue
		try:
			comm = (proc_dir / "comm").read_text(errors="ignore").strip()
			cmdline = (proc_dir / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="ignore")
		except Exception:
			continue
		if "Hyprland" not in comm and "Hyprland" not in cmdline:
			continue

		env = _read_proc_environ(pid)
		wanted = {
			"HYPRLAND_INSTANCE_SIGNATURE": env.get("HYPRLAND_INSTANCE_SIGNATURE", ""),
			"XDG_RUNTIME_DIR": env.get("XDG_RUNTIME_DIR", ""),
			"WAYLAND_DISPLAY": env.get("WAYLAND_DISPLAY", ""),
			"XDG_SESSION_TYPE": env.get("XDG_SESSION_TYPE", ""),
		}
		if wanted.get("HYPRLAND_INSTANCE_SIGNATURE") or wanted.get("XDG_RUNTIME_DIR"):
			return {k: v for k, v in wanted.items() if v}
	return None


def _effective_hyprctl_env() -> dict[str, str]:
	env = os.environ.copy()
	if os.geteuid() == 0 and not env.get("HYPRLAND_INSTANCE_SIGNATURE"):
		guessed = _guess_hyprland_env_from_sudo_user()
		if guessed:
			env.update(guessed)
	return env


def get_cursor_pos(hyprctl_path: str) -> tuple[int, int]:
	res = subprocess.run(
		[hyprctl_path, "cursorpos"],
		check=True,
		stdout=subprocess.PIPE,
		stderr=subprocess.PIPE,
		text=True,
		env=_effective_hyprctl_env(),
	)
	m = _CURSOR_RE.search(res.stdout.strip())
	if not m:
		raise RuntimeError(
			"Unexpected hyprctl cursorpos output. "
			f"stdout={res.stdout!r} stderr={res.stderr!r}"
		)
	return int(m.group(1)), int(m.group(2))


def _format_bbox(p1: tuple[int, int], p2: tuple[int, int]) -> tuple[int, int, int, int]:
	x1, y1 = p1
	x2, y2 = p2
	left = min(x1, x2)
	top = min(y1, y2)
	right = max(x1, x2)
	bottom = max(y1, y2)
	return (left, top, right - left, bottom - top)


def _by_id_keyboard_event_paths() -> list[str]:
	paths: list[str] = []
	for link in sorted(glob.glob("/dev/input/by-id/*kbd*")):
		try:
			real = os.path.realpath(link)
			if real.startswith("/dev/input/") and os.path.exists(real):
				paths.append(real)
		except Exception:
			continue
	seen: set[str] = set()
	uniq: list[str] = []
	for p in paths:
		if p not in seen:
			uniq.append(p)
			seen.add(p)
	return uniq


def pick_keyboard_device(*, key_code_hint: int) -> str:
	from evdev import InputDevice, ecodes, list_devices  # type: ignore

	def is_likely_virtual(name: str) -> bool:
		n = (name or "").lower()
		return any(s in n for s in ("ydotool", "virtual", "uinput", "dummy"))

	preferred = _by_id_keyboard_event_paths()
	for path in preferred:
		try:
			dev = InputDevice(path)
			if is_likely_virtual(dev.name or ""):
				continue
			caps = dev.capabilities().get(ecodes.EV_KEY, [])
			if key_code_hint in caps and ecodes.KEY_ENTER in caps:
				return path
		except Exception:
			continue

	candidates: list[str] = []
	for path in list_devices():
		try:
			dev = InputDevice(path)
			if is_likely_virtual(dev.name or ""):
				continue
			caps = dev.capabilities().get(ecodes.EV_KEY, [])
			if key_code_hint in caps and ecodes.KEY_ENTER in caps:
				return path
			if "keyboard" in (dev.name or "").lower() and key_code_hint in caps:
				candidates.append(path)
		except Exception:
			continue

	if candidates:
		return candidates[0]

	raise RuntimeError(
		"No keyboard-like input device found. "
		"Try: python crop.py --list-devices and pass the right one via --device /dev/input/eventX."
	)


def list_input_devices(*, key_code_hint: int) -> int:
	try:
		from evdev import InputDevice, ecodes, list_devices  # type: ignore
	except Exception as e:
		print(f"Failed to import evdev: {e}", file=sys.stderr)
		print("Install it with: pip install evdev", file=sys.stderr)
		return 1

	for path in list_devices():
		try:
			dev = InputDevice(path)
			caps = dev.capabilities().get(ecodes.EV_KEY, [])
			has_key = key_code_hint in caps
			has_enter = ecodes.KEY_ENTER in caps
			looks_keyboard = has_key and has_enter
			print(f"{path}: {dev.name}  keyboard={looks_keyboard}")
		except Exception as e:
			print(f"{path}: <error: {e}>")
	return 0


def _print_help() -> None:
	print(
		"Crop picker (Hyprland)\n"
		"- Move mouse to first corner, press 'c'\n"
		"- Move mouse to opposite corner, press 'c' again\n"
		"- Prints crop_bbox=(x, y, w, h)\n\n"
		"Keys: c=capture corner | r=reset | q/esc=quit | Ctrl+C=quit\n",
		flush=True,
	)


def run_picker_terminal(*, refresh_hz: float = 30.0) -> int:
	if not sys.stdin.isatty():
		print("stdin is not a TTY; run from a real terminal.", file=sys.stderr)
		return 2
	hyprctl_path = _require_hyprctl()
	fd = sys.stdin.fileno()
	old_settings = termios.tcgetattr(fd)
	points: list[tuple[int, int]] = []
	interval = 1.0 / max(refresh_hz, 1.0)

	_print_help()
	try:
		tty.setcbreak(fd)
		last_print = 0.0
		while True:
			rlist, _, _ = select.select([sys.stdin], [], [], interval)
			if rlist:
				ch = sys.stdin.read(1)
				if ch in ("q", "Q"):
					print("\nQuit.")
					return 0
				if ch in ("r", "R"):
					points.clear()
					print("\nReset.")
					continue
				if ch in ("c", "C"):
					pos = get_cursor_pos(hyprctl_path)
					points.append(pos)
					if len(points) == 1:
						print(f"\nCorner 1: {pos}")
					elif len(points) == 2:
						bbox = _format_bbox(points[0], points[1])
						print(f"Corner 2: {pos}")
						print(f"crop_bbox={bbox}")
						return 0
					else:
						points[:] = [pos]
						print(f"\nCorner 1: {pos}")
					continue

			now = time.time()
			if now - last_print >= interval:
				try:
					x, y = get_cursor_pos(hyprctl_path)
				except Exception as e:
					print(f"\rCursor: (error: {e})", end="", flush=True)
				else:
					status = ""
					if len(points) == 1:
						status = f" | preview crop_bbox={_format_bbox(points[0], (x, y))}"
					print(f"\rCursor: ({x}, {y}){status}   ", end="", flush=True)
				last_print = now

	except KeyboardInterrupt:
		print("\nInterrupted.")
		return 0
	finally:
		termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def run_picker_global(*, refresh_hz: float = 30.0, device: str | None = None) -> int:
	try:
		from evdev import InputDevice, ecodes  # type: ignore
	except Exception as e:
		print(f"Failed to import evdev: {e}", file=sys.stderr)
		print("Install it with: pip install evdev", file=sys.stderr)
		return 2

	hyprctl_path = _require_hyprctl()
	try:
		device_path = device or pick_keyboard_device(key_code_hint=ecodes.KEY_C)
		dev = InputDevice(device_path)
	except PermissionError:
		print(
			"Permission denied opening input device. Run with sudo or add your user to the input group.",
			file=sys.stderr,
		)
		return 1
	except Exception as e:
		print(f"Failed to open input device: {e}", file=sys.stderr)
		return 1

	print(f"Listening globally on: {dev.path} ({dev.name})")
	print("Keys: c=capture corner | r=reset | q/esc=quit | Ctrl+C=quit\n", flush=True)

	points: list[tuple[int, int]] = []
	interval = 1.0 / max(refresh_hz, 1.0)
	last_print = 0.0

	try:
		while True:
			rlist, _, _ = select.select([dev.fd], [], [], interval)
			if rlist:
				for event in dev.read():
					if event.type != ecodes.EV_KEY or event.value != 1:
						continue
					if event.code in (ecodes.KEY_Q, ecodes.KEY_ESC):
						print("\nQuit.")
						return 0
					if event.code == ecodes.KEY_R:
						points.clear()
						print("\nReset.")
						continue
					if event.code == ecodes.KEY_C:
						pos = get_cursor_pos(hyprctl_path)
						points.append(pos)
						if len(points) == 1:
							print(f"\nCorner 1: {pos}")
						elif len(points) == 2:
							bbox = _format_bbox(points[0], points[1])
							print(f"Corner 2: {pos}")
							print(f"crop_bbox={bbox}")
							return 0
						else:
							points[:] = [pos]
							print(f"\nCorner 1: {pos}")

			now = time.time()
			if now - last_print >= interval:
				try:
					x, y = get_cursor_pos(hyprctl_path)
				except Exception as e:
					print(f"\rCursor: (error: {e})", end="", flush=True)
				else:
					status = ""
					if len(points) == 1:
						status = f" | preview crop_bbox={_format_bbox(points[0], (x, y))}"
					print(f"\rCursor: ({x}, {y}){status}   ", end="", flush=True)
				last_print = now

	except KeyboardInterrupt:
		print("\nInterrupted.")
		return 0


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(description="Interactive crop_bbox picker for Hyprland (Wayland).")
	parser.add_argument(
		"--global",
		dest="global_listen",
		action="store_true",
		help="Listen globally via /dev/input (may require sudo).",
	)
	parser.add_argument(
		"--device",
		help="Input device path (e.g. /dev/input/event3). If omitted, a keyboard is auto-selected.",
	)
	parser.add_argument(
		"--list-devices",
		action="store_true",
		help="List /dev/input/event* devices (useful with --global).",
	)
	parser.add_argument(
		"--refresh-hz",
		type=float,
		default=30.0,
		help="Cursor polling rate. Higher = smoother but more hyprctl calls.",
	)

	args = parser.parse_args(argv)
	if args.list_devices:
		try:
			from evdev import ecodes  # type: ignore
		except Exception as e:
			print(f"Failed to import evdev: {e}", file=sys.stderr)
			print("Install it with: pip install evdev", file=sys.stderr)
			return 2
		return list_input_devices(key_code_hint=ecodes.KEY_C)
	if args.global_listen:
		return run_picker_global(refresh_hz=args.refresh_hz, device=args.device)
	return run_picker_terminal(refresh_hz=args.refresh_hz)


if __name__ == "__main__":
	raise SystemExit(main())