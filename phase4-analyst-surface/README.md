# Phase 4 (part 1) — Bot ↔ Database

Teach the `pe-intake` Telegram agent to answer from the canonical facts DB.

Design: the agent never touches Postgres directly. It gets a **read-only HTTP window**:

```
agent (sandbox) --GET only--> PostgREST :3005 --agent_ro role (SELECT only)--> Postgres views
```

Three locks: DB role can only read views · API accepts only GET · sandbox network
policy allows only this one endpoint.

## Setup (run in Ubuntu/WSL, in order)

### 1. Create the read-only DB role
Pick a password for `agent_ro` (add it to your .env note), then:

```bash
docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel -c \
  "CREATE ROLE agent_ro LOGIN PASSWORD 'YOUR-AGENT-RO-PASSWORD' NOSUPERUSER NOCREATEDB NOCREATEROLE;"
docker exec nemoclaw-postgres psql -U vaultstack -d enterprise_intel -c \
  "GRANT CONNECT ON DATABASE enterprise_intel TO agent_ro;
   GRANT USAGE ON SCHEMA public TO agent_ro;
   GRANT SELECT ON current_field_values, company_completeness, companies, schema_fields TO agent_ro;"
```

### 2. Start PostgREST

```bash
cd /mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/phase1-data-layer/db
export POSTGRES_PASSWORD='...'; export MINIO_PASSWORD='...'
export AGENT_RO_PASSWORD='YOUR-AGENT-RO-PASSWORD'
docker compose up -d postgrest

# smoke test from the host:
curl -s "http://localhost:3005/company_completeness"
```
Expect JSON with zoho / razorpay / dhruva-space rows.

### 3. Allow the sandbox to reach it (network policy preset)

```bash
nemoclaw pe-intake policy-add --from-file \
  "/mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/phase4-analyst-surface/policy/pe-db-preset.yaml" --yes
nemoclaw pe-intake policy-list   # should show pe-company-db
```

### 4. Give the agent the skill

```bash
openshell sandbox upload pe-intake \
  "/mnt/c/Users/PRABHAKAR/Documents/VaultStack/NemoClaw/phase4-analyst-surface/skill/company-db/SKILL.md" \
  /sandbox/.openclaw/workspace/skills/company-db/SKILL.md
```

(If upload complains about the missing folder: `nemoclaw pe-intake connect`, then
`mkdir -p /sandbox/.openclaw/workspace/skills/company-db`, `exit`, and retry.)

### 5. Test on Telegram

Ask the bot:
- "What do we know about Razorpay? Use the company database."
- "Which company profile is most complete?"
- "What's Zoho's revenue?"  → correct answer is: not in DB, founder-only by design.

Every fact should come back **with a source URL and confidence** — that's the tell
that it's reading the database, not improvising.

## Troubleshooting
- `curl` works on host but agent gets blocked → policy preset not applied; re-run step 3.
- PostgREST restarts repeatedly → role/password mismatch between step 1 and AGENT_RO_PASSWORD.
- Agent ignores the skill → start a fresh chat/session so it re-reads its workspace.
