import os
import argparse
import sys
from pathlib import Path
import json
import re

# Allow running this file directly: `python ai_models/gemini_2.py`
# so that `import ai_models.*` works even though sys.path[0] is ai_models/.
if __name__ == "__main__" and __package__ is None:
	sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dotenv

from google import genai
from google.genai import types

from ai_models.variables import sys_instruction
from ai_models.schema import resp_schema


# Load .env from the project root (parent of ai_models/)
dotenv.load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")


def generate(
	prompt: str | None = None,
	image_path: str | None = None,
	*,
	model: str = "gemini-2.5-flash",
	temperature: float = 0.75,
	enable_google_search: bool = True,
) -> str:
	parts: list[types.Part] = []

	print("Generating response...", flush=True)
	if image_path:
		with open(image_path, "rb") as f:
			parts.append(
				types.Part.from_bytes(
					data=f.read(),
					mime_type="image/png",
				)
			)

	if prompt:
		parts.append(types.Part.from_text(text=prompt))

	client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

	contents = [
		types.Content(
			role="user",
			parts=parts,
		)
	]

	tools = []
	if enable_google_search:
		tools = [types.Tool(googleSearch=types.GoogleSearch())]

	generate_content_config = types.GenerateContentConfig(
		temperature=temperature,
		
		safety_settings=[
			types.SafetySetting(
				category="HARM_CATEGORY_HARASSMENT",
				threshold="BLOCK_NONE",
			),
			types.SafetySetting(
				category="HARM_CATEGORY_HATE_SPEECH",
				threshold="BLOCK_NONE",
			),
			types.SafetySetting(
				category="HARM_CATEGORY_SEXUALLY_EXPLICIT",
				threshold="BLOCK_NONE",
			),
			types.SafetySetting(
				category="HARM_CATEGORY_DANGEROUS_CONTENT",
				threshold="BLOCK_NONE",
			),
		],
		tools=tools,
		system_instruction=[types.Part.from_text(text=sys_instruction)],
	)

	if not enable_google_search:
		generate_content_config.response_mime_type = "application/json"
		generate_content_config.response_schema = resp_schema["list_obj"]
	full_response = ""
	for chunk in client.models.generate_content_stream(
		model=model,
		contents=contents,
		config=generate_content_config,
	):
		print(f"\033[32m{chunk.text or ''}\033[0m", end="", flush=True)
		full_response += chunk.text or ""

	return full_response

def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(description="Test runner for ai_models.gemini_2")
	parser.add_argument(
		"--prompt",
		help="User prompt text. If omitted, reads from stdin.",
		default=None,
	)
	parser.add_argument(
		"--image-path",
		help="Optional image path to include as input.",
		default=None,
	)
	parser.add_argument(
		"--model",
		help="Gemini model name.",
		default="gemini-2.5-flash",
	)
	parser.add_argument(
		"--temperature",
		type=float,
		default=0.75,
	)
	parser.add_argument(
		"--no-google-search",
		action="store_true",
		help="Disable Google Search tool.",
	)

	args = parser.parse_args(argv)

	if not os.environ.get("GEMINI_API_KEY"):
		print("Missing GEMINI_API_KEY (set it in .env or environment).", file=sys.stderr)
		return 2

	prompt = args.prompt
	if prompt is None:
		prompt = sys.stdin.read().strip()

	response = generate(
		prompt=prompt,
		image_path=args.image_path,
		model=args.model,
		temperature=args.temperature,
		enable_google_search=not args.no_google_search,
	)

	# ensure newline after streaming output
	print("\n")
	# also print the full response (useful for piping/parsing)
	print(response)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
