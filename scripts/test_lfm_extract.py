import os
os.environ["NVIDIA_BASE_URL"] = "http://host.docker.internal:11435/v1"
os.environ["NVIDIA_API_KEY"] = "ollama"
import sys
sys.path.insert(0, "/app")
from nvidia_client import get_client
from extractor import SYSTEM_PROMPT, _call

client = get_client(timeout=90.0)
catalog_json = '[{"key":"hq_location","label":"HQ Location"},{"key":"founded_year","label":"Founded Year"}]'
chunk = "Tailored AI is a cybersecurity company headquartered in Mumbai, India, founded in 2024."
user = (f"TARGET COMPANY: Tailored AI\n\nFIELD CATALOG:\n{catalog_json}\n\n"
        f"SOURCE URL: https://example.com\n\nDOCUMENT TEXT:\n{chunk}\n\n"
        "Reply with the JSON array only, starting with '['.")
messages = [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
            {"role": "assistant", "content": "["}]
raw = _call(client, "oamazonasgabriel/lfm2.5-230m:bf16-8gbRAM", messages, 3000)
print("RAW _call() RESULT:", repr(raw))

r = client.chat.completions.create(model="oamazonasgabriel/lfm2.5-230m:bf16-8gbRAM", temperature=0.0, max_tokens=500, messages=messages)
msg = r.choices[0].message
print("DIRECT content:", repr(msg.content))
print("finish_reason:", r.choices[0].finish_reason)
