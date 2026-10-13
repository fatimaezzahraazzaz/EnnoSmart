__all__ = ["EnnoAmeliorationAgent"]


def __getattr__(name: str):
    if name == "EnnoAmeliorationAgent":
        from .agent import EnnoAmeliorationAgent

        return EnnoAmeliorationAgent
    raise AttributeError(name)
