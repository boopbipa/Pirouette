-- Pirouette : mise à jour « Messages » (conversations, groupes, quiz envoyés).
-- À coller une seule fois dans Supabase → SQL Editor → New query → Run (la base des défis doit déjà exister).
-- =====================================================================================================
-- ---------- Messages entre amis : conversations à deux et groupes de révision ----------
-- (Mise à jour « Messages » : pour une base créée avant, coller seulement cette partie, jusqu'à la fin.)
-- Les messages ne sont pas chiffrés de bout en bout : chacun ne lit que ses conversations (règles ci-dessous).
create table if not exists public.rooms (
  id uuid primary key default gen_random_uuid(),
  kind text not null check (kind in ('direct', 'group')),
  name text check (char_length(name) <= 60),
  created_by uuid not null references public.profiles (id) on delete cascade,
  pinned uuid,                                   -- message épinglé en haut du groupe
  created_at timestamptz not null default now()
);
alter table public.rooms enable row level security;

create table if not exists public.room_members (
  room_id uuid not null references public.rooms (id) on delete cascade,
  user_id uuid not null references public.profiles (id) on delete cascade,
  last_read timestamptz not null default now(),
  joined_at timestamptz not null default now(),
  primary key (room_id, user_id)
);
alter table public.room_members enable row level security;

create table if not exists public.messages (
  id uuid primary key default gen_random_uuid(),
  room_id uuid not null references public.rooms (id) on delete cascade,
  sender uuid not null references public.profiles (id) on delete cascade,
  body text check (char_length(body) <= 2000),
  kind text not null default 'text' check (kind in ('text', 'quiz', 'cards', 'course', 'challenge', 'result')),
  title text check (char_length(title) <= 160),
  meta jsonb,                                    -- aperçu (nombre de questions, score…)
  payload jsonb check (payload is null or octet_length(payload::text) < 2000000),  -- le quiz / les cartes envoyés
  created_at timestamptz not null default now()
);
alter table public.messages enable row level security;
create index if not exists messages_room_time on public.messages (room_id, created_at);
create index if not exists room_members_user on public.room_members (user_id);

create or replace function public.in_room(r uuid, u uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from room_members where room_id = r and user_id = u);
$$;

-- Conversations : visibles par leurs membres (et leur créateur, le temps d'y entrer)
drop policy if exists "mes conversations" on public.rooms;
create policy "mes conversations" on public.rooms for select to authenticated
  using (public.in_room(id, auth.uid()) or created_by = auth.uid());
drop policy if exists "créer une conversation" on public.rooms;
create policy "créer une conversation" on public.rooms for insert to authenticated with check (created_by = auth.uid());
drop policy if exists "renommer, épingler" on public.rooms;
create policy "renommer, épingler" on public.rooms for update to authenticated using (public.in_room(id, auth.uid()));

-- Membres : on voit les membres de ses conversations ; on y entre si on l'a créée ; un membre ajoute ses amis
drop policy if exists "membres visibles" on public.room_members;
create policy "membres visibles" on public.room_members for select to authenticated
  using (public.in_room(room_id, auth.uid()));
drop policy if exists "entrer ou ajouter un ami" on public.room_members;
create policy "entrer ou ajouter un ami" on public.room_members for insert to authenticated with check (
  (user_id = auth.uid() and exists (select 1 from public.rooms r where r.id = room_id and r.created_by = auth.uid()))
  or (public.in_room(room_id, auth.uid()) and public.are_friends(auth.uid(), user_id)));
drop policy if exists "marquer comme lu" on public.room_members;
create policy "marquer comme lu" on public.room_members for update to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());
drop policy if exists "quitter" on public.room_members;
create policy "quitter" on public.room_members for delete to authenticated using (user_id = auth.uid());

-- Messages : lus et écrits par les membres seulement, chacun en son nom
drop policy if exists "lire ses messages" on public.messages;
create policy "lire ses messages" on public.messages for select to authenticated
  using (public.in_room(room_id, auth.uid()));
drop policy if exists "écrire" on public.messages;
create policy "écrire" on public.messages for insert to authenticated
  with check (sender = auth.uid() and public.in_room(room_id, auth.uid()));

-- Un défi lancé dans une conversation : ses membres le voient et y jouent (même sans être amis avec l'auteur)
create or replace function public.sees_challenge(c uuid, u uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from challenges ch where ch.id = c and (are_friends(ch.owner, u) or exists (
    select 1 from messages m where m.kind = 'challenge' and m.meta ->> 'challenge_id' = c::text and in_room(m.room_id, u))));
$$;
drop policy if exists "défis entre amis" on public.challenges;
create policy "défis entre amis" on public.challenges for select to authenticated
  using (public.sees_challenge(id, auth.uid()));
drop policy if exists "scores du défi" on public.attempts;
create policy "scores du défi" on public.attempts for select to authenticated
  using (public.sees_challenge(challenge_id, auth.uid()));
drop policy if exists "mon score" on public.attempts;
create policy "mon score" on public.attempts for insert to authenticated
  with check (user_id = auth.uid() and public.sees_challenge(challenge_id, auth.uid()));
