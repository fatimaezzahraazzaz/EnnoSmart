"""Stop signals shared by the agent and the spending controller (no dependencies)."""


class StopResearch(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)
