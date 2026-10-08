import sys
sys.path.insert(0, "/app")
from nvidia_client import get_client
from extractor import SYSTEM_PROMPT

client = get_client(timeout=90.0)
catalog_json = '[{"key":"hq_location","label":"HQ Location"},{"key":"founded_year","label":"Founded Year"}]'
chunk = "Sequretek is a cybersecurity company headquartered in Mumbai, India, founded in 2013 by Anand Naik and Pankit Desai."
user = (f"TARGET COMPANY: Sequretek\n\nFIELD CATALOG:\n{catalog_json}\n\n"
        f"SOURCE URL: https://example.com\n\nDOCUMENT TEXT:\n{chunk}\n\n"
        "Reply with the JSON array only, starting with '['.")
messages = [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
            {"role": "assistant", "content": "["}]
r = client.chat.completions.create(model="nvidia/nvidia-nemotron-nano-9b-v2", temperature=0.0, max_tokens=3000, messages=messages)
msg = r.choices[0].message
print("CONTENT:", repr(msg.content))
print("FINISH_REASON:", r.choices[0].finish_reason)
print("HAS_REASONING:", hasattr(msg, "reasoning_content"))
if hasattr(msg, "reasoning_content"):
    print("REASONING:", repr(getattr(msg, "reasoning_content"))[:500])
