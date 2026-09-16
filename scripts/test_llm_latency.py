"""CRY-8: rough time-to-first-token latency check for candidate Groq models.

Not a rigorous benchmark -- just enough signal to pick a model for the voice
agent, where perceived responsiveness matters more than raw throughput.
"""

import time

from dotenv import load_dotenv
from groq import Groq

load_dotenv()

PROMPT = "In one short sentence, what's the weather like on Mars?"

MODELS = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
]


def time_to_first_token(model: str) -> float:
    client = Groq()
    start = time.perf_counter()
    stream = client.chat.completions.create(
        model=model,
        max_tokens=100,
        messages=[{"role": "user", "content": PROMPT}],
        stream=True,
    )
    for chunk in stream:
        if chunk.choices[0].delta.content:
            return time.perf_counter() - start
    return time.perf_counter() - start


if __name__ == "__main__":
    for model in MODELS:
        ttft = time_to_first_token(model)
        print(f"{model}: {ttft * 1000:.0f} ms to first token")
