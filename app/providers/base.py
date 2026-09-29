from __future__ import annotations


class ProviderError(RuntimeError):
    """Erreur lisible par l'utilisateur, renvoyée telle quelle à l'interface."""


class QuotaError(ProviderError):
    """Le service ne peut plus rien générer pour l'instant (crédit épuisé, limite atteinte) : inutile de lancer les
    créations suivantes avec ce même moteur, elles sont annulées."""


class NothingNew(ProviderError):
    """Rien de nouveau à ajouter (tout ressemble à ce qui existe déjà) : la création se met en pause et l'étudiant
    choisit la suite (des questions plus faciles, réessayer…) pendant que les suivantes continuent."""
