"""CRY-8: rough time-to-first-token latency check for candidate LLMs.

Not a rigorous benchmark -- just enough signal to pick a model for the voice
agent, where perceived responsiveness matters more than raw throughput.
"""

import time

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

PROMPT = "In one short sentence, what's the weather like on Mars?"


def time_to_first_token_anthropic(model: str) -> float:
    client = Anthropic()
    start = time.perf_counter()
    first_token_at = None
    with client.messages.stream(
        model=model,
        max_tokens=100,
        messages=[{"role": "user", "content": PROMPT}],
    ) as stream:
        for _ in stream.text_stream:
            if first_token_at is None:
                first_token_at = time.perf_counter()
                break
    return (first_token_at or time.perf_counter()) - start


if __name__ == "__main__":
    for model in ["claude-haiku-4-5-20251001"]:
        ttft = time_to_first_token_anthropic(model)
        print(f"{model}: {ttft * 1000:.0f} ms to first token")
