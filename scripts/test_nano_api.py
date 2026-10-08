from nvidia_client import get_client

c = get_client(timeout=90.0)
for m in ["nvidia/nvidia-nemotron-nano-9b-v2", "nvidia/nemotron-3-ultra-550b-a55b"]:
    try:
        r = c.chat.completions.create(model=m, max_tokens=300, messages=[{"role": "user", "content": "Reply with exactly the word OK and nothing else."}])
        msg = r.choices[0].message
        print("MODEL:", m)
        print("  CONTENT:", repr(msg.content))
        print("  HAS_REASONING:", hasattr(msg, "reasoning_content") or hasattr(msg, "reasoning"))
    except Exception as e:
        print("FAIL:", m, "->", str(e)[:200])
