import sys
sys.path.insert(0, "/app")
from nvidia_client import get_client
from extractor import SYSTEM_PROMPT, _call

client = get_client(timeout=90.0)
catalog_json = '[{"key":"hq_location","label":"HQ Location"},{"key":"founded_year","label":"Founded Year"}]'
chunk = "Sequretek is a cybersecurity company headquartered in Mumbai, India, founded in 2013 by Anand Naik and Pankit Desai."
user = (f"TARGET COMPANY: Sequretek\n\nFIELD CATALOG:\n{catalog_json}\n\n"
        f"SOURCE URL: https://example.com\n\nDOCUMENT TEXT:\n{chunk}\n\n"
        "Reply with the JSON array only, starting with '['.")
messages = [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
            {"role": "assistant", "content": "["}]
raw = _call(client, "nvidia/nvidia-nemotron-nano-9b-v2", messages, 3000)
print("RAW _call() RESULT (what extractor.py actually sees):", repr(raw))
