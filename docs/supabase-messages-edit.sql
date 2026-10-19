-- Pirouette : mise à jour « Modifier ou supprimer ses messages ».
-- À coller une seule fois dans Supabase → SQL Editor → New query → Run (la messagerie doit déjà exister).

-- ---------- Modifier ou supprimer ses messages ----------
-- (Mise à jour « Modifier un message » : pour une base qui a déjà les Messages, coller seulement cette partie.)
alter table public.messages add column if not exists edited_at timestamptz;
drop policy if exists "modifier mon message" on public.messages;
create policy "modifier mon message" on public.messages for update to authenticated
  using (sender = auth.uid()) with check (sender = auth.uid());
drop policy if exists "supprimer mon message" on public.messages;
create policy "supprimer mon message" on public.messages for delete to authenticated using (sender = auth.uid());
