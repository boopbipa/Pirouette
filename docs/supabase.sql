-- Pirouette en ligne : comptes, amis et défi du jour (Supabase).
-- À coller une seule fois dans Supabase → SQL Editor → New query → Run.
--
-- Ce qui est en ligne : un pseudo, un code ami, les défis (10 questions de quiz, sans le cours lui-même)
-- et les scores. Les cours (PDF…) restent sur l'ordinateur de chacun.
-- Chaque table est protégée (Row Level Security) : on ne lit que ce qui nous concerne.

-- ---------- Profils ----------
create table if not exists public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  pseudo text not null check (char_length(pseudo) between 2 and 24),
  friend_code text not null unique default upper(substr(md5(random()::text), 1, 6)),
  created_at timestamptz not null default now()
);
alter table public.profiles enable row level security;

-- ---------- Amis ----------
create table if not exists public.friendships (
  user_id uuid not null references public.profiles (id) on delete cascade,
  friend_id uuid not null references public.profiles (id) on delete cascade,
  status text not null default 'pending' check (status in ('pending', 'accepted')),
  created_at timestamptz not null default now(),
  primary key (user_id, friend_id),
  check (user_id <> friend_id)
);
alter table public.friendships enable row level security;

-- Deux personnes sont amies quand l'une a invité l'autre et que l'invitation est acceptée
create or replace function public.are_friends(a uuid, b uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select a = b or exists (
    select 1 from friendships
    where status = 'accepted' and ((user_id = a and friend_id = b) or (user_id = b and friend_id = a)));
$$;

-- ---------- Défis ----------
create table if not exists public.challenges (
  id uuid primary key default gen_random_uuid(),
  owner uuid not null references public.profiles (id) on delete cascade,
  title text not null check (char_length(title) <= 120),
  subject text check (char_length(subject) <= 120),
  questions jsonb not null check (jsonb_typeof(questions) = 'array' and jsonb_array_length(questions) between 1 and 20),
  day date not null default current_date,
  time_limit int check (time_limit between 30 and 3600),  -- chrono pour tout le défi (secondes), facultatif
  created_at timestamptz not null default now()
);
alter table public.challenges enable row level security;

create table if not exists public.attempts (
  challenge_id uuid not null references public.challenges (id) on delete cascade,
  user_id uuid not null references public.profiles (id) on delete cascade,
  score int not null check (score >= 0),
  total int not null check (total > 0 and score <= total),
  seconds int check (seconds >= 0),
  created_at timestamptz not null default now(),
  primary key (challenge_id, user_id)  -- un seul essai par défi
);
alter table public.attempts enable row level security;

-- ---------- Règles d'accès ----------
-- Profils : chacun voit les profils (pseudo, code ami) pour pouvoir ajouter un ami ; ne modifie que le sien
drop policy if exists "profils lisibles" on public.profiles;
create policy "profils lisibles" on public.profiles for select to authenticated using (true);
drop policy if exists "mon profil" on public.profiles;
create policy "mon profil" on public.profiles for insert to authenticated with check (id = auth.uid());
drop policy if exists "mon profil modifiable" on public.profiles;
create policy "mon profil modifiable" on public.profiles for update to authenticated using (id = auth.uid());

-- Amis : on voit ses invitations (envoyées et reçues), on invite en son nom, on accepte ce qu'on reçoit
drop policy if exists "mes amitiés" on public.friendships;
create policy "mes amitiés" on public.friendships for select to authenticated
  using (user_id = auth.uid() or friend_id = auth.uid());
drop policy if exists "inviter" on public.friendships;
create policy "inviter" on public.friendships for insert to authenticated
  with check (user_id = auth.uid() and status = 'pending');
drop policy if exists "accepter" on public.friendships;
create policy "accepter" on public.friendships for update to authenticated
  using (friend_id = auth.uid()) with check (friend_id = auth.uid());
drop policy if exists "retirer" on public.friendships;
create policy "retirer" on public.friendships for delete to authenticated
  using (user_id = auth.uid() or friend_id = auth.uid());

-- Défis : visibles par leur auteur et ses amis ; chacun publie et supprime les siens
drop policy if exists "défis entre amis" on public.challenges;
create policy "défis entre amis" on public.challenges for select to authenticated
  using (public.are_friends(owner, auth.uid()));
drop policy if exists "publier un défi" on public.challenges;
create policy "publier un défi" on public.challenges for insert to authenticated with check (owner = auth.uid());
drop policy if exists "supprimer mon défi" on public.challenges;
create policy "supprimer mon défi" on public.challenges for delete to authenticated using (owner = auth.uid());

-- Scores : visibles par ceux qui voient le défi ; chacun enregistre seulement le sien, une fois
drop policy if exists "scores du défi" on public.attempts;
create policy "scores du défi" on public.attempts for select to authenticated
  using (exists (select 1 from public.challenges c where c.id = challenge_id and public.are_friends(c.owner, auth.uid())));
drop policy if exists "mon score" on public.attempts;
create policy "mon score" on public.attempts for insert to authenticated
  with check (user_id = auth.uid()
              and exists (select 1 from public.challenges c where c.id = challenge_id and public.are_friends(c.owner, auth.uid())));

create index if not exists challenges_owner_day on public.challenges (owner, day desc);
create index if not exists attempts_user on public.attempts (user_id);

-- Mise à jour (chrono des défis) : pour une base créée avant, cette ligne suffit
alter table public.challenges add column if not exists time_limit int check (time_limit between 30 and 3600);

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

-- ---------- Modifier ou supprimer ses messages ----------
-- (Mise à jour « Modifier un message » : pour une base qui a déjà les Messages, coller seulement cette partie.)
alter table public.messages add column if not exists edited_at timestamptz;
drop policy if exists "modifier mon message" on public.messages;
create policy "modifier mon message" on public.messages for update to authenticated
  using (sender = auth.uid()) with check (sender = auth.uid());
drop policy if exists "supprimer mon message" on public.messages;
create policy "supprimer mon message" on public.messages for delete to authenticated using (sender = auth.uid());
