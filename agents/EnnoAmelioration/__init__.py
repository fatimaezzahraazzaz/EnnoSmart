"""EnnoAmelioration — révision contrôlée et traçable des dossiers CIR."""

__all__ = ["EnnoAmeliorationAgent"]


def __getattr__(name: str):
    # Import tardif : analyser une structure de document ne doit pas initialiser
    # le client LLM ni ses dépendances réseau.
    if name == "EnnoAmeliorationAgent":
        from .application.agent import EnnoAmeliorationAgent

        return EnnoAmeliorationAgent
    raise AttributeError(name)
