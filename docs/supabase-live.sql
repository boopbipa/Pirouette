-- Pirouette : mise à jour « Quiz en direct » (la même question pour tous les joueurs, en même temps).
-- À coller une seule fois dans Supabase → SQL Editor → New query → Run (la mise à jour « Messages » doit déjà y être).
-- =====================================================================================================

-- Une partie vit dans une conversation : ses membres la voient et la rejoignent ; seul l'hôte la fait avancer.
create table if not exists public.live_games (
  id uuid primary key default gen_random_uuid(),
  room_id uuid not null references public.rooms (id) on delete cascade,
  host uuid not null references public.profiles (id) on delete cascade,
  title text check (char_length(title) <= 120),
  questions jsonb not null,
  state text not null default 'lobby' check (state in ('lobby', 'question', 'reveal', 'done')),
  current int not null default 0,
  created_at timestamptz not null default now()
);
alter table public.live_games enable row level security;

create table if not exists public.live_players (
  game_id uuid not null references public.live_games (id) on delete cascade,
  user_id uuid not null references public.profiles (id) on delete cascade,
  joined_at timestamptz not null default now(),
  primary key (game_id, user_id)
);
alter table public.live_players enable row level security;

create table if not exists public.live_answers (
  game_id uuid not null references public.live_games (id) on delete cascade,
  user_id uuid not null references public.profiles (id) on delete cascade,
  q int not null,
  choice text,
  correct boolean not null default false,
  points int not null default 0 check (points between 0 and 1000),
  created_at timestamptz not null default now(),
  primary key (game_id, user_id, q)
);
alter table public.live_answers enable row level security;

create or replace function public.in_live(g uuid, u uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from live_games lg where lg.id = g and in_room(lg.room_id, u));
$$;

drop policy if exists "parties de mes conversations" on public.live_games;
create policy "parties de mes conversations" on public.live_games for select to authenticated
  using (public.in_room(room_id, auth.uid()));
drop policy if exists "lancer une partie" on public.live_games;
create policy "lancer une partie" on public.live_games for insert to authenticated
  with check (host = auth.uid() and public.in_room(room_id, auth.uid()));
drop policy if exists "l'hôte fait avancer" on public.live_games;
create policy "l'hôte fait avancer" on public.live_games for update to authenticated
  using (host = auth.uid()) with check (host = auth.uid());

drop policy if exists "joueurs visibles" on public.live_players;
create policy "joueurs visibles" on public.live_players for select to authenticated using (public.in_live(game_id, auth.uid()));
drop policy if exists "rejoindre" on public.live_players;
create policy "rejoindre" on public.live_players for insert to authenticated
  with check (user_id = auth.uid() and public.in_live(game_id, auth.uid()));

drop policy if exists "réponses visibles" on public.live_answers;
create policy "réponses visibles" on public.live_answers for select to authenticated using (public.in_live(game_id, auth.uid()));
drop policy if exists "répondre" on public.live_answers;
create policy "répondre" on public.live_answers for insert to authenticated
  with check (user_id = auth.uid() and public.in_live(game_id, auth.uid()));

-- Le message qui invite à la partie, dans la conversation
alter table public.messages drop constraint if exists messages_kind_check;
alter table public.messages add constraint messages_kind_check
  check (kind in ('text', 'quiz', 'cards', 'course', 'challenge', 'result', 'live'));
