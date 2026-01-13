from __future__ import annotations

import os
import shutil
import subprocess
import glob
import pwd
from pathlib import Path


def _in_wayland_session() -> bool:
	return bool(os.environ.get("WAYLAND_DISPLAY")) and (os.environ.get("XDG_SESSION_TYPE") == "wayland")


def _guess_wayland_env_from_sudo_user() -> dict[str, str] | None:
	"""Best-effort reconstruction of Wayland env when running under sudo.

	When this process is root (via sudo), common session vars like WAYLAND_DISPLAY
	and XDG_RUNTIME_DIR are often stripped. We can often recover them for the
	original user by looking for the Wayland socket in /run/user/<uid>/.
	"""
	if os.geteuid() != 0:
		return None
	user = os.environ.get("SUDO_USER")
	if not user:
		return None
	try:
		uid = pwd.getpwnam(user).pw_uid
	except KeyError:
		return None

	runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"
	sockets = sorted(glob.glob(os.path.join(runtime_dir, "wayland-*")))
	if not sockets:
		return None

	wayland_display = os.path.basename(sockets[0])
	return {
		"XDG_SESSION_TYPE": "wayland",
		"XDG_RUNTIME_DIR": runtime_dir,
		"WAYLAND_DISPLAY": wayland_display,
	}


def _default_output_path(directory: str | os.PathLike[str] | None = None) -> Path:
	base_dir = Path(directory) if directory else Path.cwd()
	base_dir.mkdir(parents=True, exist_ok=True)
	# v1
 	# ts = datetime.now().strftime("%Y%m%d_%H%M%S")
	# return base_dir / f"screenshot_{ts}.png"
	# v2
	return base_dir / "screenshot.png"


def take_fullscreen_screenshot(
	*,
	output_path: str | os.PathLike[str] | None = None,
	directory: str | os.PathLike[str] | None = None,
	session_env: dict[str, str] | None = None,
) -> str:
	"""Take a whole-screen screenshot on Linux.

	- Wayland: uses `grim` (recommended on Arch).
	- X11: uses the `mss` Python package.

	Returns the absolute path to the written PNG.
	"""

	effective_env = os.environ.copy()
	if session_env:
		# Caller can explicitly pass through GUI/session vars.
		effective_env.update({k: v for k, v in session_env.items() if v is not None})

	# When running under sudo, GUI/session variables are often stripped. Try to recover
	# Wayland env for the original user if possible.
	if not effective_env.get("WAYLAND_DISPLAY") and not effective_env.get("DISPLAY"):
		guessed = _guess_wayland_env_from_sudo_user()
		if guessed:
			effective_env.update(guessed)

	# Without DISPLAY (X11) or WAYLAND_DISPLAY (Wayland), screenshot tools cannot connect.
	if not effective_env.get("DISPLAY") and not effective_env.get("WAYLAND_DISPLAY"):
		raise RuntimeError(
			"No GUI session detected (missing DISPLAY/WAYLAND_DISPLAY). "
			"If you started this program with sudo, preserve your session env (e.g. `sudo -E`) "
			"or pass DISPLAY/WAYLAND_DISPLAY/XDG_RUNTIME_DIR/XAUTHORITY through sudo."
		)

	out = Path(output_path) if output_path else _default_output_path(directory)
	out = out.expanduser().resolve()

	if bool(effective_env.get("WAYLAND_DISPLAY")) and (effective_env.get("XDG_SESSION_TYPE") == "wayland"):
		if not effective_env.get("XDG_RUNTIME_DIR"):
			raise RuntimeError(
				"Wayland session detected but XDG_RUNTIME_DIR is missing. "
				"This usually happens when running under sudo without preserving the user session environment."
			)
		grim = shutil.which("grim")
		if not grim:
			raise RuntimeError(
				"Wayland session detected but `grim` is not installed. "
				"Install it with: sudo pacman -S grim"
			)
		out.parent.mkdir(parents=True, exist_ok=True)
		subprocess.run([grim, str(out)], check=True, env=effective_env)
		return str(out)

	# X11 path
	if not effective_env.get("DISPLAY"):
		raise RuntimeError(
			"X11 screenshot requested but DISPLAY is missing. "
			"If running under sudo, use `sudo -E` or pass DISPLAY and XAUTHORITY through."
		)
	try:
		import mss  # type: ignore
		import mss.tools  # type: ignore
	except Exception as e:
		raise RuntimeError(
			"X11 session detected but Python package `mss` is not available. "
			"Install it with: pip install mss"
		) from e

	out.parent.mkdir(parents=True, exist_ok=True)
	with mss.mss() as sct:
		monitor = sct.monitors[0]  # 0 = all monitors combined
		img = sct.grab(monitor)
		mss.tools.to_png(img.rgb, img.size, output=str(out))
	return str(out)


def take_screenshot2(
	*,
	output_path: str | os.PathLike[str] | None = None,
	directory: str | os.PathLike[str] | None = None,
	session_env: dict[str, str] | None = None,
) -> str:
	"""Wayland-only fullscreen screenshot using `grim`.

	This is a stricter version intended for Wayland setups.
	It will:
	- Prefer the provided `session_env`.
	- If running under sudo and env is stripped, try to recover Wayland env using SUDO_USER.

	Returns the absolute path to the written PNG.
	"""
	effective_env = os.environ.copy()
	if session_env:
		effective_env.update({k: v for k, v in session_env.items() if v is not None})

	if not effective_env.get("WAYLAND_DISPLAY") or (effective_env.get("XDG_SESSION_TYPE") != "wayland"):
		guessed = _guess_wayland_env_from_sudo_user()
		if guessed:
			effective_env.update(guessed)

	if not effective_env.get("WAYLAND_DISPLAY"):
		raise RuntimeError(
			"Wayland screenshot requested but WAYLAND_DISPLAY is missing. "
			"If running under sudo, use `sudo -E` or pass WAYLAND_DISPLAY and XDG_RUNTIME_DIR through."
		)
	if effective_env.get("XDG_SESSION_TYPE") != "wayland":
		raise RuntimeError(
			"Wayland screenshot requested but XDG_SESSION_TYPE is not 'wayland'. "
			"(If you are on Wayland, pass XDG_SESSION_TYPE=wayland through sudo.)"
		)
	if not effective_env.get("XDG_RUNTIME_DIR"):
		raise RuntimeError(
			"Wayland screenshot requested but XDG_RUNTIME_DIR is missing. "
			"This usually happens when running under sudo without preserving the user session environment."
		)

	grim = shutil.which("grim")
	if not grim:
		raise RuntimeError(
			"Wayland screenshot requires `grim`, but it was not found in PATH. "
			"Install it (e.g. Arch: sudo pacman -S grim)."
		)

	out = Path(output_path) if output_path else _default_output_path(directory)
	out = out.expanduser().resolve()
	out.parent.mkdir(parents=True, exist_ok=True)
	subprocess.run([grim, str(out)], check=True, env=effective_env)
	return str(out)


def main() -> int:
	path = take_fullscreen_screenshot(directory="img")
	print(path)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
