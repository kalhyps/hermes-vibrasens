"""Plugin web « routeur » (clé de plugin : web/routeur)."""
from .provider import RouteurWebProvider


def register(ctx) -> None:
    ctx.register_web_search_provider(RouteurWebProvider(ctx))
