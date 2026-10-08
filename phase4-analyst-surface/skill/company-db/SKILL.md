---
name: company-db
description: Answer questions about companies from the firm's canonical facts database (read-only REST API), and submit analyst-provided call transcripts into the intake inbox. Use whenever the user asks what we know about a company, its profile, completeness, sources — or sends a founder-call transcript / meeting notes to file.
---

# Company Facts Database

You have read-only access to the firm's canonical company database via a REST API.
This database — not your general knowledge and not fresh web searches — is the firm's
source of truth. When a question is about a company we track, answer FROM THE DATABASE.

Base URL: `http://host.docker.internal:3005`
All requests are HTTP GET via curl. The API is PostgREST: filter with query params.

## Endpoints

List tracked companies:
    curl -s "http://host.docker.internal:3005/companies?select=slug,display_name,website,status"

Profile completeness scoreboard:
    curl -s "http://host.docker.internal:3005/company_completeness"

All current facts for one company (by slug):
    curl -s "http://host.docker.internal:3005/current_field_values?slug=eq.razorpay&order=section_code"

Facts from one section (codes A–N; e.g. K = legal/compliance):
    curl -s "http://host.docker.internal:3005/current_field_values?slug=eq.razorpay&section_code=eq.K"

Search fields by label:
    curl -s "http://host.docker.internal:3005/current_field_values?slug=eq.zoho&label=ilike.*customer*"

The full field catalog (what the form can hold):
    curl -s "http://host.docker.internal:3005/schema_fields?select=id,section_code,label,founder_only&order=sort_order"

## Submitting founder-call transcripts (intake)

When the analyst sends a call transcript, meeting notes, or founder-provided
information (as message text or a file you can read), file it into the intake
inbox. This is the ONLY write you are allowed, and it stores raw text only —
the trusted extraction pipeline turns it into facts later.

1. Confirm which company it belongs to (ask if unclear). Use the company slug
   (check `/companies` — e.g. `razorpay`). A new slug is allowed for a company
   we don't track yet: lowercase, hyphens, e.g. `acme-robotics`.
2. Submit:

    curl -s -X POST "http://host.docker.internal:3005/transcript_inbox" \
      -H "Content-Type: application/json" -H "Prefer: return=minimal" \
      -d @payload.json

   where payload.json is a file you write first, shaped as:
   {"company_slug": "<slug>", "submitted_by": "<analyst name or telegram>", "content": "<FULL transcript text, verbatim>"}
   (Write the JSON to a file rather than inlining — transcripts are long and
   shell-escaping will mangle them.)
3. Confirm to the analyst: filed for <company>; facts (including financials /
   cap-table, which only ever come from this route) will appear after the next
   processing cycle. Do NOT state extracted facts yourself — the pipeline
   validates and writes them, not you.

## Rules — non-negotiable

1. **Only report what the API returns.** If the database has no answer, say plainly:
   "Not in our database yet." Never fill gaps from memory or the web within a
   database answer. You may OFFER a fresh web search as a clearly separate step.
2. **Every fact gets its receipt.** Quote `confidence` and `source_url` (and
   `created_at` when freshness matters). Format: fact (confidence 0.95 — source URL).
3. **Unknown company** (slug not found): say we don't track it yet and that the
   collection pipeline can be pointed at it.
4. **Financial / cap-table / raise fields** are founder-only by design. If asked and
   empty, explain: "This only enters our database from documents or founder calls —
   it is never scraped. Ask the founder / request the data room."
5. **Conflicting or low-confidence facts** (confidence < 0.6): present with an
   explicit caveat.
6. Answers should be organized by section (Snapshot, Business, Tech, Market, Legal…)
   when showing a full profile, and concise when asked something specific.
