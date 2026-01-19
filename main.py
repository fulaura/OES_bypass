#!/usr/bin/env python3
from __future__ import annotations

import argparse
import glob
import os
import sys
import termios
import tty



import json

from screenshot import take_fullscreen_screenshot, take_screenshot2
from ocr import ocr
from ai import generate
from mouseclick import click_bbox
from exp.resp_to_cb import copy_to_clipboard

"""Listen for global keyboard events and react to the `p` key.

Requires:
        pip install evdev

Notes:
        - Reading /dev/input/event* may require root or input-group permissions.
        - Run with: sudo python print_on_press.py
        - Optionally choose device: sudo python print_on_press.py --device /dev/input/event3
"""
def bbox_for_contains(ocr_results, needle: str, strict: bool = False):
    n = needle.strip().lower()
    for idx, item in enumerate(ocr_results):
            ###########################
        if strict:
                n_items = len(ocr_results)
                l=[]
                for a in range(idx+1, min(idx+4,n_items)):
                        l.append(ocr_results[a]['text'].strip().lower())
                combined = " ".join(l)
                print("Combined text:", combined, flush=True)
                if n in combined:
                        print("Found in combined text", flush=True)
                        return ocr_results[idx+3]['bbox']
                ###############################
        if n in item["text"].lower():
            return item["bbox"]
    return None

def find_answer():
        take_screenshot2(directory="img")
        print("screenshot taken", flush=True)
        ocr_results = ocr(image_path="./img/screenshot.png", mode="chunk", visualize=True, x_thresh=20, y_thresh=4)
        model_response = generate(image_path="./img/screenshot.png", prompt="")
        model_response = json.loads(model_response)
        print("Model response:", model_response, flush=True)
        print(type(model_response), flush=True)
        for i in model_response["Correct option"]:
                bbox = bbox_for_contains(ocr_results, i)
                if bbox is None:
                        print(f"Could not find bbox for answer option: {i!r}", file=sys.stderr)
                        print("Trying next option...")
                        bbox = bbox_for_contains(ocr_results, i, strict=True)
                        if bbox is None:
                                print(f"Could not find bbox for answer option on second try, skipping: {i!r}", file=sys.stderr)
                                continue
                print(f"Clicking answer option: {i!r} at bbox: {bbox}")
                click_bbox(bbox, rule="random", backend="uinput", 
                           move_duration=0.2, move_steps=15)
                
        print("\n\nFull response:\n", model_response["Correct option"])
        # try:
        #         session_env = {
        #                 "WAYLAND_DISPLAY": os.environ.get("WAYLAND_DISPLAY", ""),
        #                 "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR", ""),
        #                 "XDG_SESSION_TYPE": os.environ.get("XDG_SESSION_TYPE", ""),
        #                 "DISPLAY": os.environ.get("DISPLAY", ""),
        #                 "XAUTHORITY": os.environ.get("XAUTHORITY", ""),
        #         }
        #         path = take_fullscreen_screenshot(directory="img", session_env=session_env)
        #         print(f"screenshot taken: {path}")
        # except Exception as e:
        #         print(f"Failed to take screenshot on 'p': {e}", file=sys.stderr)
        #         print(
        #                 "Hint: if you're running with sudo for --global, sudo often strips GUI/session env. "
        #                 "Try `sudo -E` (or pass DISPLAY/WAYLAND_DISPLAY/XDG_RUNTIME_DIR/XAUTHORITY) and ensure dependencies exist: "
        #                 "Wayland->`grim`, X11->`pip install mss`.",
        #                 file=sys.stderr,
        #         )
        #         return

def ans_cp():
        take_screenshot2(directory="img")
        print("screenshot taken", flush=True)
        model_response = generate(image_path="./img/screenshot.png", prompt="Give answer to given question with details. Respond in JSON format like {\"Correct option\": \"<answer>\"}")
        model_response = json.loadso(model_response)
        print("Model response:", model_response, flush=True)
        for i in model_response["Correct option"]:
                copy_to_clipboard(i)
        print("\n\nFull response copied to clipboard:\n")
        
def _by_id_keyboard_event_paths() -> list[str]:
        paths: list[str] = []
        for link in sorted(glob.glob("/dev/input/by-id/*kbd*")):
                try:
                        real = os.path.realpath(link)
                        if real.startswith("/dev/input/") and os.path.exists(real):
                                paths.append(real)
                except Exception:
                        continue
        # De-duplicate while preserving order
        seen: set[str] = set()
        uniq: list[str] = []
        for p in paths:
                if p not in seen:
                        uniq.append(p)
                        seen.add(p)
        return uniq


def pick_keyboard_device() -> str:
        """Pick a likely keyboard device path from /dev/input/event*."""
        from evdev import InputDevice, ecodes, list_devices

        def is_likely_virtual(name: str) -> bool:
                n = (name or "").lower()
                return any(s in n for s in ("ydotool", "virtual", "uinput", "dummy"))

        # Best signal on most distros: stable by-id symlinks that include "kbd".
        preferred = _by_id_keyboard_event_paths()
        for path in preferred:
                try:
                        dev = InputDevice(path)
                        if is_likely_virtual(dev.name or ""):
                                continue
                        caps = dev.capabilities().get(ecodes.EV_KEY, [])
                        if ecodes.KEY_P in caps and ecodes.KEY_ENTER in caps:
                                return path
                except Exception:
                        continue

        # Fallback: scan all event devices and look for a device that can emit KEY_P.
        candidates: list[str] = []
        for path in list_devices():
            try:
                dev = InputDevice(path)
                if is_likely_virtual(dev.name or ""):
                        continue
                caps = dev.capabilities().get(ecodes.EV_KEY, [])
                if ecodes.KEY_P in caps and ecodes.KEY_ENTER in caps:
                        return path
                if "keyboard" in (dev.name or "").lower() and ecodes.KEY_P in caps:
                        candidates.append(path)
            except Exception:
                continue

        if candidates:
                return candidates[0]

        raise RuntimeError(
                "No keyboard-like input device found. "
                "Try: python print_on_press.py --list-devices  and pass the right one via --device /dev/input/eventX."
        )


def list_input_devices() -> int:
        """Print input devices and whether they look like a keyboard."""
        try:
                from evdev import InputDevice, ecodes, list_devices
        except Exception as e:
                print(f"Failed to import evdev: {e}", file=sys.stderr)
                return 1

        for path in list_devices():
                try:
                        dev = InputDevice(path)
                        caps = dev.capabilities().get(ecodes.EV_KEY, [])
                        has_p = ecodes.KEY_P in caps
                        has_enter = ecodes.KEY_ENTER in caps
                        looks_keyboard = has_p and has_enter
                        print(f"{path}: {dev.name}  keyboard={looks_keyboard}")
                except Exception as e:
                        print(f"{path}: <error: {e}>")
        return 0


def listen_terminal(*, debug: bool = False) -> int:
        """Listen for keypresses on the current terminal (no sudo required).

        This only receives keys while the terminal window is focused.
        """
        if not sys.stdin.isatty():
                print(
                        "stdin is not a TTY, so this mode can't read keypresses. "
                        "Run it from a real terminal (VS Code: Terminal panel), not the Debug Console.",
                        file=sys.stderr,
                )
                return 2

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)

        print("Listening for 'p' on this terminal (focus this terminal). Ctrl+C to exit...", flush=True)
        try:
                tty.setraw(fd)
                while True:
                        ch = sys.stdin.read(1)
                        if not ch:
                                return 0
                        if debug:
                                print(f"got: {ch!r} (ord={ord(ch)})", flush=True)
                        if ch in ("p", "P"):
                                find_answer()
                                print("p is pressed", flush=True)
                        if ch in ("o", "O"):
                                ans_cp()
                                print(f"{ch} is pressed", flush=True)
        except KeyboardInterrupt:
                return 0
        finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def listen_global(device: str | None) -> int:
        """Listen for keypresses globally via /dev/input (may require sudo)."""
        from evdev import InputDevice, ecodes

        try:
                device_path = device or pick_keyboard_device()
                dev = InputDevice(device_path)
        except PermissionError:
                print(
                        "Permission denied opening input device. Try: sudo python print_on_press.py --global",
                        file=sys.stderr,
                )
                return 1
        except Exception as e:
                print(f"Failed to open input device: {e}", file=sys.stderr)
                return 1

        print(f"Listening globally on: {dev.path} ({dev.name})")
        print("Press 'p' anywhere (Ctrl+C to exit)...")

        try:
                for event in dev.read_loop():
                        if event.type == ecodes.EV_KEY and event.value == 1 and event.code == ecodes.KEY_P:
                                print("p is pressed", flush=True)
                                find_answer()
                        if event.type == ecodes.EV_KEY and event.value == 1 and event.code == ecodes.KEY_O:
                                print("o is pressed", flush=True)
                                ans_cp()
        except KeyboardInterrupt:
                pass

        return 0


def main() -> int:
        parser = argparse.ArgumentParser(
                description=(
                        "Print 'p is pressed' whenever you press the p key. "
                        "By default listens only in this terminal; use --global for system-wide listening."
                )
        )
        parser.add_argument(
                "--global",
                dest="global_listen",
                action="store_true",
                help="Listen globally via /dev/input (may require sudo).",
        )
        parser.add_argument(
                "--debug",
                action="store_true",
                help="Print every character received (terminal mode only).",
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
        args = parser.parse_args()

        if args.list_devices:
                return list_input_devices()

        if args.global_listen:
                return listen_global(args.device)
        return listen_terminal(debug=args.debug)


if __name__ == "__main__":
        raise SystemExit(main())
    
    
    
    
