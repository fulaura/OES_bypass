
#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from typing import Any


def _guess_hypr_env_from_sudo_user() -> dict[str, str] | None:
	"""Best-effort reconstruction of Hyprland env when running under sudo.

	Hyprland's `hyprctl` typically needs:
	- XDG_RUNTIME_DIR (usually /run/user/<uid>)
	- HYPRLAND_INSTANCE_SIGNATURE

	When you run a Python program under sudo, these are often stripped.
	"""
	if os.geteuid() != 0:
		return None
	user = os.environ.get("SUDO_USER")
	if not user:
		return None
	try:
		import pwd

		uid = pwd.getpwnam(user).pw_uid
	except Exception:
		return None

	runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"
	hypr_dir = os.path.join(runtime_dir, "hypr")
	if not os.path.isdir(hypr_dir):
		return None

	try:
		entries = sorted(os.listdir(hypr_dir))
	except Exception:
		return None
	if not entries:
		return None

	# Pick the first signature directory.
	signature = entries[0]
	return {
		"XDG_RUNTIME_DIR": runtime_dir,
		"HYPRLAND_INSTANCE_SIGNATURE": signature,
	}


def _hyprctl_json(cmd: str) -> Any:
	if not shutil.which("hyprctl"):
		raise RuntimeError("hyprctl not found in PATH")

	env = os.environ.copy()
	if os.geteuid() == 0:
		if not env.get("XDG_RUNTIME_DIR") or not env.get("HYPRLAND_INSTANCE_SIGNATURE"):
			guessed = _guess_hypr_env_from_sudo_user()
			if guessed:
				env.update(guessed)

	res = subprocess.run(
		["hyprctl", "-j", cmd],
		env=env,
		capture_output=True,
		text=True,
	)
	if res.returncode != 0:
		msg = (res.stderr or res.stdout or "").strip()
		raise RuntimeError(f"hyprctl failed: {msg}")
	try:
		return json.loads(res.stdout)
	except Exception as e:
		raise RuntimeError(f"hyprctl returned non-JSON: {res.stdout!r}") from e


def get_cursor_pos() -> tuple[int, int]:
	"""Return current cursor position in Hyprland global coords."""
	data = _hyprctl_json("cursorpos")
	return (int(data.get("x")), int(data.get("y")))


def move_cursor_uinput(
	*,
	x: int,
	y: int,
	ui: Any | None = None,
	duration: float = 0.10,
	steps: int = 12,
	debug: bool = False,
) -> None:
	"""Move cursor smoothly to (x,y) via /dev/uinput.

	This is a deterministic "animation" (ease-in/out), not random / "human" motion.
	It uses `hyprctl cursorpos` feedback to remain accurate even with acceleration.

	Requires python-evdev and permissions for /dev/uinput (often root).
	"""
	try:
		from evdev import UInput, ecodes  # type: ignore
	except Exception as e:
		raise RuntimeError("evdev is required for uinput movement (pip install evdev)") from e

	steps = max(int(steps), 1)
	duration = max(float(duration), 0.0)
	per_step_sleep = (duration / steps) if steps > 0 else 0.0

	start_x, start_y = get_cursor_pos()
	if debug:
		print(f"move: start=({start_x},{start_y}) target=({x},{y}) steps={steps} duration={duration}", flush=True)

	def ease(t: float) -> float:
		# Smoothstep: 3t^2 - 2t^3
		return t * t * (3.0 - 2.0 * t)

	cap = {
		ecodes.EV_REL: [ecodes.REL_X, ecodes.REL_Y],
		ecodes.EV_KEY: [ecodes.BTN_LEFT, ecodes.BTN_RIGHT, ecodes.BTN_MIDDLE],
	}
	step_cap = 400
	tolerance = 2

	def _do_move(active_ui: Any) -> None:
		# Animated movement toward the target
		for i in range(1, steps + 1):
			t = i / steps
			k = ease(t)
			step_target_x = int(round(start_x + (int(x) - start_x) * k))
			step_target_y = int(round(start_y + (int(y) - start_y) * k))

			cur_x, cur_y = get_cursor_pos()
			dx = step_target_x - int(cur_x)
			dy = step_target_y - int(cur_y)
			mx = max(-step_cap, min(step_cap, dx))
			my = max(-step_cap, min(step_cap, dy))
			if mx:
				active_ui.write(ecodes.EV_REL, ecodes.REL_X, mx)
			if my:
				active_ui.write(ecodes.EV_REL, ecodes.REL_Y, my)
			active_ui.syn()
			if per_step_sleep:
				time.sleep(per_step_sleep)

		# Final short correction
		for _ in range(6):
			cur_x, cur_y = get_cursor_pos()
			dx = int(x) - int(cur_x)
			dy = int(y) - int(cur_y)
			if abs(dx) <= tolerance and abs(dy) <= tolerance:
				break
			mx = max(-step_cap, min(step_cap, dx))
			my = max(-step_cap, min(step_cap, dy))
			if mx:
				active_ui.write(ecodes.EV_REL, ecodes.REL_X, mx)
			if my:
				active_ui.write(ecodes.EV_REL, ecodes.REL_Y, my)
			active_ui.syn()
			time.sleep(0.006)

	if ui is None:
		with UInput(cap, name="OES_bypass_uinput_mouse") as created_ui:
			_do_move(created_ui)
	else:
		_do_move(ui)

	if debug:
		end_x, end_y = get_cursor_pos()
		print(f"move: end=({end_x},{end_y})", flush=True)


def create_uinput_mouse(*, name: str = "OES_bypass_uinput_mouse") -> Any:
	"""Create a UInput device suitable for both cursor movement and clicks."""
	try:
		from evdev import UInput, ecodes  # type: ignore
	except Exception as e:
		raise RuntimeError("evdev is required for uinput (pip install evdev)") from e

	cap = {
		ecodes.EV_REL: [ecodes.REL_X, ecodes.REL_Y],
		ecodes.EV_KEY: [ecodes.BTN_LEFT, ecodes.BTN_RIGHT, ecodes.BTN_MIDDLE],
	}
	return UInput(cap, name=name)

