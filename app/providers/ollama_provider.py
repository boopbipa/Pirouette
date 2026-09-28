"""Version locale : génération avec Ollama, qui tourne sur le GPU (Metal) du Mac."""

from __future__ import annotations

import json
import os
import re

import httpx

from ..quiz import SYSTEM_PROMPT, QuizOptions, build_user_prompt, chunk_text, plan_chunks, quiz_schema
from ..revision import CARDS_SCHEMA, CARDS_SYSTEM, build_cards_prompt
from .base import ProviderError

# Mémoire de lecture (« contexte », en tokens) : choisie dans Réglages → IA locale, sinon selon la mémoire du Mac
# (32 768 tokens avec 18 Go). Pirouette l'envoie à chaque demande : pas besoin de Modelfile.
CONTEXT: int | None = None


def context_size() -> int:
    if os.getenv("OLLAMA_NUM_CTX"):
        return int(os.environ["OLLAMA_NUM_CTX"])
    if CONTEXT:
        return CONTEXT
    from ..local_ai import ram_gb, recommended_context

    return recommended_context(ram_gb())


def chunk_chars() -> int:
    """Taille des morceaux de cours : ~1,5 caractère par token de contexte (≈ 40 % du contexte pour le cours,
    le reste pour les consignes et la réponse). 32 768 tokens → ~49 000 caractères, un cours de 8 000 mots d'un coup."""
    return int(os.getenv("OLLAMA_CHUNK_CHARS") or context_size() * 3 // 2)

# Modèles qui « réfléchissent » avant de répondre (qwen3, deepseek-r1…) : réflexion coupée par défaut
# (Réglages → IA locale). Elle rallonge beaucoup la génération et fait chauffer le Mac, pour un gain faible ici.
THINKING = False
_thinking_models: dict[str, bool] = {}


def ollama_url() -> str:
    return os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")


def default_model() -> str:
    """Modèle imposé par OLLAMA_MODEL, sinon celui conseillé pour la mémoire de cet ordinateur."""
    if os.getenv("OLLAMA_MODEL"):
        return os.environ["OLLAMA_MODEL"]
    from ..local_ai import MODELS, ram_gb, recommended_model

    return (recommended_model(ram_gb()) or MODELS[0])["name"]


async def list_models() -> list[str] | None:
    """Modèles installés, ou None si Ollama ne répond pas."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{ollama_url()}/api/tags")
            response.raise_for_status()
    except httpx.HTTPError:
        return None
    return sorted(m["name"] for m in response.json().get("models", []))


async def generate(course_text: str, options: QuizOptions, model: str | None = None, on_progress=None) -> list[dict]:
    return await run(
        course_text, SYSTEM_PROMPT, quiz_schema(options.types),
        lambda chunk, n, part: build_user_prompt(chunk, n, options, part),
        total_items=options.num_questions, model=model, on_progress=on_progress, unit="questions",
    )


async def run(course_text: str, system: str, schema: dict, build_prompt, total_items: int | None = None,
              model: str | None = None, on_progress=None, unit: str = "") -> list[dict]:
    """Traite le cours morceau par morceau (la fenêtre de contexte d'un modèle local est limitée).

    `build_prompt(morceau, nb_éléments, partie)` construit la demande pour chaque morceau. Avec `total_items`,
    les éléments (questions, cartes…) sont répartis entre les morceaux ; sinon chaque morceau est traité en entier.
    """
    model = model or default_model()
    chunks = chunk_text(course_text, chunk_chars())
    plan = plan_chunks(chunks, total_items) if total_items else [(c, 0) for c in chunks]
    parts: list[dict] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(900, connect=5)) as client:
        for index, (chunk, n_items) in enumerate(plan, start=1):
            if on_progress:
                detail = f" ({n_items} {unit})" if total_items else ""
                await on_progress(f"Partie {index}/{len(plan)} du cours{detail}…")
            part = f"partie {index}/{len(plan)}" if len(plan) > 1 else ""
            parts.append(await _chat(client, model, schema, [
                {"role": "system", "content": system},
                {"role": "user", "content": build_prompt(chunk, n_items, part)},
            ]))
    return parts


async def generate_cards(course_text: str, n_cards: int, language: str, model: str | None = None,
                         on_progress=None, avoid=(), definition_rule=None, focus="") -> list[dict]:
    return await run(course_text, CARDS_SYSTEM, CARDS_SCHEMA,
                     lambda chunk, n, part: build_cards_prompt(chunk, n, language, part, avoid, definition_rule, focus),
                     total_items=n_cards, model=model, on_progress=on_progress, unit="cartes")


async def ask(system: str, schema: dict, prompt: str, model: str | None = None) -> dict:
    """Une petite demande structurée, en un seul appel (ex. : repérer les chapitres)."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=5)) as client:
        return await _chat(client, model or default_model(), schema, [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ])


async def _chat(client: httpx.AsyncClient, model: str, schema: dict, messages: list[dict],
                num_ctx: int | None = None) -> dict:
    payload = {
        "model": model,
        "stream": False,
        "format": schema,
        "options": {"num_ctx": num_ctx or context_size(), "temperature": 0.4},
        "messages": messages,
    }
    if await _can_think(client, model):
        payload["think"] = THINKING  # seulement pour ces modèles : les autres refusent l'option
    try:
        response = await client.post(f"{ollama_url()}/api/chat", json=payload)
    except httpx.ConnectError as exc:
        raise ProviderError(
            "Impossible de joindre Ollama. Lance l'application Ollama (ou `ollama serve`) puis réessaie."
        ) from exc
    except httpx.TimeoutException as exc:
        raise ProviderError("Ollama a mis trop de temps à répondre. Essaie un modèle plus petit.") from exc

    if response.status_code == 404:
        raise ProviderError(f"Le modèle « {model} » n'est pas installé. Lance : ollama pull {model}")
    if response.status_code >= 400:
        raise ProviderError(f"Erreur Ollama ({response.status_code}) : {response.text[:300]}")

    content = response.json().get("message", {}).get("content", "")
    return parse_json(content)


async def _can_think(client: httpx.AsyncClient, model: str) -> bool:
    """Le modèle sait-il « réfléchir » ? (capacité « thinking » annoncée par Ollama, gardée en mémoire)"""
    if model not in _thinking_models:
        try:
            response = await client.post(f"{ollama_url()}/api/show", json={"model": model}, timeout=10)
            _thinking_models[model] = response.status_code == 200 and "thinking" in response.json().get("capabilities", [])
        except (httpx.HTTPError, ValueError):
            return False
    return _thinking_models[model]


def parse_json(content: str) -> dict:
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        if not match:
            raise ProviderError("Le modèle local n'a pas renvoyé de JSON exploitable. Réessaie ou change de modèle.")
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise ProviderError("Le modèle local a renvoyé un JSON invalide. Réessaie ou change de modèle.") from exc
    if not isinstance(data, dict):
        raise ProviderError("Réponse inattendue du modèle local.")
    return data
