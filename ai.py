import argparse
import importlib
import os
import sys
import time
from pathlib import Path

import dotenv

dotenv.load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")


def _load_model_module(model_name: str):
    if not model_name:
        raise ValueError("model_name must be a non-empty string")

    # Convention: ai_models/<model_name>.py exports generate(prompt, image_path, **kwargs)
    module_name = f"ai_models.{model_name}"
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            f"AI model module '{module_name}' not found. "
            f"Set AI_MODEL to a valid module name in ./ai_models (e.g. 'gemini_3')."
        ) from exc


def generate(
    prompt: str | None = None,
    image_path: str | None = None,
    model: str | None = None,
    **kwargs,
) -> str:
    model_name = model or os.environ.get("AI_MODEL", "gemini_3")
    model_module = _load_model_module(model_name)

    model_generate = getattr(model_module, "generate", None)
    if not callable(model_generate):
        raise AttributeError(
            f"AI model module 'ai_models.{model_name}' must define a callable 'generate' function"
        )
    grounding= False if model_name == "gemini_2" else True
    print(f"using google search: {grounding}", flush=True)
    return model_generate(prompt=prompt, image_path=image_path, enable_google_search=grounding, **kwargs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Call Gemini and print JSON response.")
    parser.add_argument(
        "--prompt",
        help="User prompt text. If omitted, reads from stdin.",
        default=None,
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=2,
        help="Retry count for transient network errors.",
    )
    args = parser.parse_args(argv)

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Missing GEMINI_API_KEY (set it in .env or environment).", file=sys.stderr)
        return 2

    prompt = args.prompt
    if prompt is None:
        prompt = sys.stdin.read().strip()
    if not prompt:
        print("No prompt provided. Use --prompt or pipe via stdin.", file=sys.stderr)
        return 2

    for attempt in range(args.retries + 1):
        try:
            generate(prompt)
            return 0
        except Exception as exc:  # keep broad: httpx/httpcore types vary by version
            message = str(exc)
            is_transient = "Connection reset by peer" in message or "ConnectError" in message
            if attempt < args.retries and is_transient:
                time.sleep(1.0 * (attempt + 1))
                continue
            raise

if __name__ == "__main__":
    raise SystemExit(main())
