-- Pirouette : mise à jour « iPhone et iPad » (tes quiz et tes flashcards, aussi sur ton téléphone et ta tablette).
-- À coller une seule fois dans Supabase → SQL Editor → New query → Run.
--
-- Ce qui est en ligne : tes quiz, tes flashcards et où tu en es (jamais les cours eux-mêmes : PDF, textes…).
-- Seul le Mac écrit les quiz et les cartes ; l'iPhone et l'iPad y sont « jumelés » (un code affiché par le Mac)
-- et peuvent les lire, et y ajouter leurs révisions (que le Mac reprend ensuite chez lui).
-- =====================================================================================================

-- Un iPhone ou un iPad jumelé à un compte (le compte du Mac : « owner »)
create table if not exists public.sync_devices (
  device uuid primary key references auth.users (id) on delete cascade,
  owner uuid not null references auth.users (id) on delete cascade,
  name text check (char_length(name) <= 60),
  created_at timestamptz not null default now(),
  check (device <> owner)
);
alter table public.sync_devices enable row level security;

-- Le compte dont on voit les quiz : le sien, ou celui du Mac auquel cet appareil est jumelé
create or replace function public.sync_owner() returns uuid
language sql stable security definer set search_path = public as $$
  select coalesce((select owner from sync_devices where device = auth.uid()), auth.uid());
$$;

-- Les quiz, les paquets de cartes et le reste (matières, semestres), un document par ligne
create table if not exists public.sync_docs (
  owner uuid not null default auth.uid() references auth.users (id) on delete cascade,
  kind text not null check (kind in ('meta', 'deck', 'quiz')),
  id text not null check (char_length(id) <= 80),
  hash text not null,
  data jsonb not null,
  updated_at timestamptz not null default now(),
  primary key (owner, kind, id)
);
alter table public.sync_docs enable row level security;

-- Les révisions faites sur l'iPhone ou l'iPad, en attendant que le Mac les reprenne
create table if not exists public.sync_events (
  id uuid primary key,
  owner uuid not null references auth.users (id) on delete cascade,
  device uuid not null default auth.uid(),
  kind text not null check (kind in ('card', 'answers', 'attempt')),
  payload jsonb not null,
  created_at timestamptz not null default now()
);
alter table public.sync_events enable row level security;

-- Les codes de jumelage, valables 15 minutes
create table if not exists public.sync_pairs (
  code text primary key check (char_length(code) between 6 and 12),
  owner uuid not null default auth.uid() references auth.users (id) on delete cascade,
  expires_at timestamptz not null default now() + interval '15 minutes'
);
alter table public.sync_pairs enable row level security;

drop policy if exists "mes documents" on public.sync_docs;
create policy "mes documents" on public.sync_docs for select to authenticated using (owner = public.sync_owner());
drop policy if exists "le Mac écrit" on public.sync_docs;
create policy "le Mac écrit" on public.sync_docs for all to authenticated
  using (owner = auth.uid()) with check (owner = auth.uid());

drop policy if exists "mes révisions" on public.sync_events;
create policy "mes révisions" on public.sync_events for select to authenticated using (owner = public.sync_owner());
drop policy if exists "réviser" on public.sync_events;
create policy "réviser" on public.sync_events for insert to authenticated
  with check (owner = public.sync_owner() and device = auth.uid());
drop policy if exists "le Mac reprend" on public.sync_events;
create policy "le Mac reprend" on public.sync_events for delete to authenticated using (owner = auth.uid());

drop policy if exists "mes appareils" on public.sync_devices;
create policy "mes appareils" on public.sync_devices for select to authenticated
  using (owner = auth.uid() or device = auth.uid());
drop policy if exists "retirer un appareil" on public.sync_devices;
create policy "retirer un appareil" on public.sync_devices for delete to authenticated
  using (owner = auth.uid() or device = auth.uid());

drop policy if exists "créer un code" on public.sync_pairs;
create policy "créer un code" on public.sync_pairs for insert to authenticated with check (owner = auth.uid());
drop policy if exists "effacer mes codes" on public.sync_pairs;
create policy "effacer mes codes" on public.sync_pairs for delete to authenticated using (owner = auth.uid());

-- L'iPhone ou l'iPad tape le code affiché par le Mac : il est jumelé à ce compte (le code ne sert qu'une fois)
create or replace function public.sync_pair(pair_code text, device_name text default null) returns uuid
language plpgsql security definer set search_path = public as $$
declare
  found uuid;
begin
  delete from sync_pairs where expires_at < now();
  select owner into found from sync_pairs where code = upper(replace(pair_code, '-', ''));
  if found is null then
    raise exception 'Code inconnu ou expiré : demande un nouveau code sur ton Mac.';
  end if;
  if found = auth.uid() then
    raise exception 'Ce code vient de cet appareil.';
  end if;
  delete from sync_pairs where code = upper(replace(pair_code, '-', ''));
  insert into sync_devices (device, owner, name) values (auth.uid(), found, left(device_name, 60))
    on conflict (device) do update set owner = excluded.owner, name = excluded.name, created_at = now();
  return found;
end;
$$;
grant execute on function public.sync_pair(text, text) to authenticated;
