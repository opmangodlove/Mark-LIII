-- docs/supabase_schema.sql — run once in the Supabase SQL Editor (free tier).
-- Creates the task mirror used by memory/supabase_sync.py.
--
-- RLS is ENABLED but no anon policies are added: only the service_role key
-- (held in config/supabase.json on your PC) can read/write. Your Supabase
-- anon key alone can do nothing here.

create table if not exists jarvis_tasks (
  id         text primary key,              -- 8-char id from actions/manage_tasks.py
  title      text not null,
  status     text not null default 'todo',  -- todo | in_progress | blocked | done
  priority   text not null default 'medium',-- low | medium | high | critical
  tags       jsonb not null default '[]'::jsonb,
  created    text not null default '',
  updated    text not null default '',      -- "YYYY-MM-DD HH:MM" last-writer-wins stamp
  synced_at  timestamptz not null default now()
);

alter table jarvis_tasks enable row level security;

-- No policies for anon/authenticated on purpose: the service key bypasses RLS.
-- (Revoke public access explicitly so the table is invisible without the key.)
revoke all on jarvis_tasks from anon;
revoke all on jarvis_tasks from authenticated;

-- Realtime: subscribed dashboards see inserts/updates/deletes live.
-- (Free tier allows one realtime stream; a task board is far under it.)
alter publication supabase_realtime add table jarvis_tasks;

-- Optional: keep the mirror tidy if you delete the local board wholesale.
-- create policy "service full access" on jarvis_tasks
--   for all using (auth.role() = 'service_role');
