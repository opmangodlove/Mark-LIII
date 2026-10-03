# Supabase Cloud Sync (free tier) — setup

Sync the JARVIS task board across devices with a free Supabase project.
**Everything works without this** — the local JSON store (`memory/tasks.json`)
is always the source of truth. This is an optional mirror.

## 1. Create the project

1. Go to [supabase.com](https://supabase.com) → sign up free → **New project**
2. Pick any name, a DB password (save it somewhere), and a region near you.
   The free tier includes 2 projects, 500 MB database — far more than a task board needs.

## 2. Create the table

Open **SQL Editor** → **New query** → paste the whole contents of
[`docs/supabase_schema.sql`](../docs/supabase_schema.sql) from this repo → **Run**.

This creates the `jarvis_tasks` table with row-level security enabled and **no
public policies** — only the service key can touch it. It also adds the table
to the realtime publication.

## 3. Save the keys

**Project Settings → API**:

- copy **Project URL** — looks like `https://abcdefgh.supabase.co`
- copy **service_role** key (*not* the anon key — the anon key can't see the table)

Create `Mark-LIII/config/supabase.json`:

```json
{
  "url": "https://abcdefgh.supabase.co",
  "service_key": "eyJhbGciOi...your-service-role-key..."
}
```

`config/` is already gitignored (same place your Gemini key lives), so the
service key never leaves the machine or enters version control.

## 4. Install the client and restart

```bash
pip install supabase
python main.py
```

You should see `[Supabase] sync active` in the console once at startup.

## How it behaves

| Event | What happens |
|---|---|
| Voice: "add a task to fix the login bug" | Task lands in `memory/tasks.json`, dashboard updates instantly, board upserts to Supabase in a background thread |
| Phone dashboard: drag a card to Done | Same dispatch as voice → local file + dashboard + Supabase |
| JARVIS starts with Supabase configured | Remote rows **newer** than the local board are pulled in (union merge — local-only tasks are kept) |
| Supabase is down / no internet | Push silently skips; local board unaffected; next mutation re-pushes |
| `config/supabase.json` missing | Sync is completely off — zero network calls, zero cost |

## Troubleshooting

- **`[Supabase] table check failed`** — the SQL from step 2 didn't run, or the keys point at a different project.
- **Nothing appears in Supabase** — you copied the **anon** key instead of the **service_role** key. The anon key is blocked by RLS by design.
- **pip install supabase fails** — sync stays off; nothing else in JARVIS depends on it.
