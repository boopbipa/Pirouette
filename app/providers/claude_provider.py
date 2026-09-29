"""Version API : génération avec Claude via l'API Anthropic."""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager

import anthropic

from ..quiz import SYSTEM_PROMPT, QuizOptions, build_user_prompt, quiz_schema
from ..revision import CARDS_SCHEMA, CARDS_SYSTEM, build_cards_prompt
from .base import ProviderError, QuotaError

# Claude lit jusqu'à ~1M tokens : un cours entier tient en un seul appel.
# Au-delà de cette taille on préfère prévenir plutôt que tronquer le cours en silence.
MAX_CHARS = 2_500_000


def default_model() -> str:
    return os.getenv("CLAUDE_MODEL", "claude-opus-5")


def is_configured() -> bool:
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    has_key = bool(key) and key != "sk-ant-..."  # ignore la valeur d'exemple de .env.example
    return has_key or bool(os.getenv("ANTHROPIC_AUTH_TOKEN"))


async def check_key() -> str:
    """Vérifie la clé API sans rien générer (lecture de la fiche du modèle, gratuite)."""
    if not is_configured():
        raise ProviderError("Aucune clé API enregistrée.")
    async with _api_errors(None):
        model = await anthropic.AsyncAnthropic().models.retrieve(default_model())
    return model.display_name


async def generate(course_text: str, options: QuizOptions, model: str | None = None, on_progress=None) -> list[dict]:
    return await run(course_text, SYSTEM_PROMPT, quiz_schema(options.types),
                     build_user_prompt(course_text, options.num_questions, options), model, on_progress,
                     "Claude lit le cours et rédige les questions…")


async def run(course_text: str, system: str, schema: dict, prompt: str, model: str | None = None,
              on_progress=None, progress_message: str = "Claude lit le cours…") -> list[dict]:
    """Un seul appel : Claude lit le cours entier (jusqu'à ~1M tokens)."""
    if len(course_text) > MAX_CHARS:
        raise ProviderError("Le cours est trop long pour un seul appel. Retire des fichiers du cours.")
    if not is_configured():
        raise ProviderError("Clé API manquante : ajoute ta clé Claude dans Réglages.")
    if on_progress:
        await on_progress(progress_message)

    async with _api_errors(model):
        # Streaming : évite les timeouts HTTP sur les longs cours / longues réponses.
        async with anthropic.AsyncAnthropic().beta.messages.stream(
            model=model or default_model(),
            max_tokens=64000,
            thinking={"type": "adaptive"},
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
            # Si Claude décline la requête, l'API la relance automatiquement sur le modèle de repli recommandé.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            message = await stream.get_final_message()
    return [_parse(message)]


async def generate_cards(course_text: str, n_cards: int, language: str, model: str | None = None,
                         on_progress=None, avoid=(), definition_rule=None, focus="") -> list[dict]:
    return await run(course_text, CARDS_SYSTEM, CARDS_SCHEMA,
                     build_cards_prompt(course_text, n_cards, language, avoid=avoid, definition_rule=definition_rule,
                                        focus=focus),
                     model, on_progress, "Claude lit le cours et prépare les cartes…")


async def ask(system: str, schema: dict, prompt: str, model: str | None = None) -> dict:
    """Une petite demande structurée, en un seul appel (ex. : repérer les chapitres)."""
    if not is_configured():
        raise ProviderError("Clé API manquante : ajoute ta clé Claude dans Réglages.")
    async with _api_errors(model):
        message = await anthropic.AsyncAnthropic().beta.messages.create(
            model=model or default_model(),
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    return _parse(message)


def _parse(message) -> dict:
    if message.stop_reason == "refusal":
        raise ProviderError("Claude a refusé de traiter ce contenu.")
    if message.stop_reason == "max_tokens":
        raise ProviderError("La réponse a été coupée : demande moins de questions ou de cartes.")
    text = next((block.text for block in message.content if block.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderError("Réponse de Claude illisible. Réessaie.") from exc


@asynccontextmanager
async def _api_errors(model: str | None):
    try:
        yield
    except anthropic.AuthenticationError as exc:
        raise ProviderError("Clé API Claude refusée par Anthropic : vérifie qu'elle est complète et toujours active (Réglages).") from exc
    except anthropic.PermissionDeniedError as exc:
        raise ProviderError("Ta clé API n'a pas accès à ce modèle.") from exc
    except anthropic.NotFoundError as exc:
        raise ProviderError(f"Modèle Claude introuvable : {model or default_model()}.") from exc
    except anthropic.RateLimitError as exc:
        raise QuotaError("Limite d'utilisation de l'API Claude atteinte : réessaie dans quelques minutes, ou passe à "
                         "l'IA locale.") from exc
    except anthropic.APIStatusError as exc:
        detail = exc.body.get("error", {}).get("message") if isinstance(exc.body, dict) else None
        if "credit balance" in (detail or exc.message or "").lower() or exc.status_code == 402:
            raise QuotaError("Ton crédit Claude API est épuisé : recharge-le sur console.anthropic.com (Billing), ou "
                             "passe à l'IA locale.") from exc
        if exc.status_code in (529, 503):
            raise QuotaError("Claude est surchargé en ce moment : réessaie dans quelques minutes, ou passe à l'IA locale.") from exc
        raise ProviderError(f"Erreur de l'API Claude ({exc.status_code}) : {detail or exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise ProviderError("Impossible de joindre l'API Claude. Vérifie ta connexion internet.") from exc
