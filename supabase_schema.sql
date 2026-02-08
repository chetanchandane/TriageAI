-- Run this in the Supabase SQL editor to create tables for TriageAI auth and messages.
-- Profiles: patient_id and full_name per auth user (linked to auth.users).

create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  full_name text,
  patient_id text unique not null,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

-- Create profile automatically on signup (avoids RLS: app cannot insert as new user until session is set).
drop trigger if exists on_auth_user_created on auth.users;
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, full_name, patient_id)
  values (
    new.id,
    coalesce(new.raw_user_meta_data->>'full_name', new.email, 'Patient'),
    'PAT-' || upper(replace(substring(new.id::text from 1 for 8), '-', ''))
  );
  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- Messages: one row per patient message, with triage result and patient context.
create table if not exists public.messages (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  patient_id text not null,
  full_name text,
  email text,
  content text not null,
  triage_result jsonb default '{}',
  created_at timestamptz default now()
);

create index if not exists idx_messages_user_id on public.messages(user_id);
create index if not exists idx_messages_patient_id on public.messages(patient_id);
create index if not exists idx_messages_created_at on public.messages(created_at desc);

-- RLS: allow users to read/write their own profile and messages; staff could be given broader access via a role.
alter table public.profiles enable row level security;
alter table public.messages enable row level security;

create policy "Users can read own profile"
  on public.profiles for select
  using (auth.uid() = id);

create policy "Users can update own profile"
  on public.profiles for update
  using (auth.uid() = id);

create policy "Users can insert own profile"
  on public.profiles for insert
  with check (auth.uid() = id);

create policy "Users can read own messages"
  on public.messages for select
  using (auth.uid() = user_id);

create policy "Users can insert own messages"
  on public.messages for insert
  with check (auth.uid() = user_id);

-- Staff: to let staff see all messages, either:
-- 1) Use a backend (e.g. separate Streamlit app or API) with SUPABASE_SERVICE_ROLE_KEY, or
-- 2) Add a policy: create policy "Staff can read all messages" on public.messages for select using (auth.jwt() ->> 'role' = 'staff');
--    and set the role in your auth provider or custom claims for staff users.
