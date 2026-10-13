from __future__ import annotations

from abc import ABC, abstractmethod

from ..domain.models import ArticleIdentity, FulltextCandidate
from ..infrastructure.http import ResilientHttpClient


class FulltextProvider(ABC):
    name: str = "base"
    priority: int = 100

    def enabled(self) -> bool:
        return True

    def disabled_reason(self) -> str | None:
        return None

    @abstractmethod
    async def search(
        self,
        article: ArticleIdentity,
        http: ResilientHttpClient,
    ) -> list[FulltextCandidate]:
        raise NotImplementedError
