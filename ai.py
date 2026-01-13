import argparse
import json
import os
import sys
import time

import dotenv
from variables import sys_instruction

dotenv.load_dotenv()

from google import genai
from google.genai import types




def generate(prompt: str = None, 
             image_path: str | None = None) -> None:
    
    parts = []
    print("Generating response...", flush=True)
    if image_path:
        parts.append(types.Part.from_bytes(
        data=open(image_path, "rb").read(),
        mime_type="image/png",
    ))

    if prompt:
        parts.append(types.Part.from_text(text=prompt))
        
        
    client = genai.Client(
        api_key=os.environ.get("GEMINI_API_KEY"),
    )

    model = "gemini-3-flash-preview"
    contents = [
        types.Content(
            role="user",
            parts=parts,
        ),
    ]
    tools = [
        types.Tool(googleSearch=types.GoogleSearch(
        )),
    ]
    generate_content_config = types.GenerateContentConfig(
        temperature=0.75,
        thinking_config=types.ThinkingConfig(
            thinking_level="MEDIUM",
        ),
        safety_settings=[
            types.SafetySetting(
                category="HARM_CATEGORY_HARASSMENT",
                threshold="BLOCK_NONE",  # Block none
            ),
            types.SafetySetting(
                category="HARM_CATEGORY_HATE_SPEECH",
                threshold="BLOCK_NONE",  # Block none
            ),
            types.SafetySetting(
                category="HARM_CATEGORY_SEXUALLY_EXPLICIT",
                threshold="BLOCK_NONE",  # Block none
            ),
            types.SafetySetting(
                category="HARM_CATEGORY_DANGEROUS_CONTENT",
                threshold="BLOCK_NONE",  # Block none
            ),
        ],
        tools=tools,
        response_mime_type="application/json",
        response_schema=genai.types.Schema(
            type = genai.types.Type.OBJECT,
            properties = {
                "Correct option": genai.types.Schema(
                    type = genai.types.Type.STRING,
                ),
            },
        ),
        system_instruction=[
            types.Part.from_text(text=sys_instruction),
        ],
    )

    full_response = ""
    
    for chunk in client.models.generate_content_stream(
        model=model,
        contents=contents,
        config=generate_content_config,
    ):
        # print(chunk.text or "", end="", flush=True)
        print(f"\033[32m{chunk.text or ''}\033[0m", end="", flush=True)
        full_response += chunk.text or ""
    
    return full_response


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
