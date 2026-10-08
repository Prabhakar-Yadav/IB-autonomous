"""Validation quality tools: source tiers, normalization, LLM equivalence judge.

Tier 0 = founder transcript (highest trust)
Tier 1 = company's own site, government/regulators
Tier 2 = major press, Wikipedia
Tier 3 = data aggregators (PitchBook, Tracxn, Crunchbase…)
Tier 4 = blogs, social, unknown (lowest)
Lower number = more trusted. A worse source never displaces a better one.
"""
import re
from urllib.parse import urlparse

TIER2_DOMAINS = {
    "reuters.com", "bloomberg.com", "ft.com", "wsj.com", "cnbc.com",
    "economictimes.com", "timesofindia.indiatimes.com", "thehindu.com",
    "livemint.com", "moneycontrol.com", "business-standard.com",
    "techcrunch.com", "forbes.com", "wikipedia.org", "yourstory.com",
    "inc42.com", "entrepreneur.com", "financialexpress.com",
}
TIER2_DOMAINS |= {
    # registry mirrors republishing official MCA data
    "zaubacorp.com", "tofler.in", "thecompanycheck.com", "falconebiz.com",
    "instafinancials.com", "indiafilings.com", "startupindia.gov.in",
    "cleartax.in", "vakilsearch.com", "corpseed.com", "legalwiz.in",
    # official API of the company's own public code
    "github.com", "api.github.com",
    # per user 2026-07-19: LinkedIn (company/people pages) and Y Combinator into tier 2
    "linkedin.com", "ycombinator.com",
    # more credible press / registries found via research
    "growthlist.co", "opencorporates.com", "gleif.org", "sec.gov",
    "companieshouse.gov.uk", "find-and-update.company-information.service.gov.uk",
    "patentscope.wipo.int", "espacenet.com", "ipindia.gov.in",
}
TIER3_DOMAINS = {
    "pitchbook.com", "tracxn.com", "crunchbase.com", "cbinsights.com",
    "owler.com", "getlatka.com", "clay.com", "commonroom.io",
    "zoominfo.com", "apollo.io",
}
GOV_HINTS = (".gov", ".gov.in", ".nic.in", "rbi.org.in", "sebi.gov.in",
             "mca.gov.in", "mcacdm.nic.in", "data.gov.in", "sec.gov", "europa.eu",
             "gleif.org", "companieshouse.gov.uk", "uspto.gov", "epo.org",
             "ipindia.gov.in", "gst.gov.in", "nseindia.com", "bseindia.com")


def _domain(url: str) -> str:
    d = urlparse(url or "").netloc.lower()
    return d[4:] if d.startswith("www.") else d


def source_tier(url: str, own_domain: str = "") -> int:
    if not url:
        return 4
    if url.startswith("transcript://"):
        return 0
    d = _domain(url)
    if not d:
        return 4
    if own_domain and (d == own_domain or d.endswith("." + own_domain)):
        return 1
    if any(h in d for h in GOV_HINTS):
        return 1
    for t2 in TIER2_DOMAINS:
        if d == t2 or d.endswith("." + t2):
            return 2
    for t3 in TIER3_DOMAINS:
        if d == t3 or d.endswith("." + t3):
            return 3
    return 4


def norm(s: str) -> str:
    """Cheap text normalization so trivial phrasing differences don't conflict."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())).strip()


JUDGE_SYSTEM = """You compare two recorded values for the same company data field.
Reply with EXACTLY ONE WORD:
SAME            - they state the same fact (possibly reworded)
A_MORE_PRECISE  - they agree, but value A is more precise/complete
B_MORE_PRECISE  - they agree, but value B is more precise/complete
DIFFERENT       - they genuinely contradict each other or describe different things
No other output."""


def judge_equivalence(client, model: str, field_label: str, value_a: str, value_b: str) -> str:
    """Returns SAME | A_MORE_PRECISE | B_MORE_PRECISE | DIFFERENT (safe default)."""
    try:
        resp = client.chat.completions.create(
            model=model, temperature=0.0, max_tokens=10,
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM},
                {"role": "user", "content":
                    f"Field: {field_label}\nValue A: {value_a[:800]}\nValue B: {value_b[:800]}"},
            ],
        )
        word = (resp.choices[0].message.content or "").strip().upper().split()[0]
        if word in ("SAME", "A_MORE_PRECISE", "B_MORE_PRECISE", "DIFFERENT"):
            return word
    except Exception:
        pass
    return "DIFFERENT"
