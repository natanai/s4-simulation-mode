class SimulationModeSettings:
    def __init__(
        self,
        enabled=False,
        protect_motives=True,
        allow_pregnancy=False,
        auto_unpause=True,
        tick_seconds=10,
        motive_floor=-60,
        motive_bump_to=-10,
    ):
        self.enabled = enabled
        self.protect_motives = protect_motives
        self.allow_pregnancy = allow_pregnancy
        self.auto_unpause = auto_unpause
        self.tick_seconds = tick_seconds
        self.motive_floor = motive_floor
        self.motive_bump_to = motive_bump_to


settings = SimulationModeSettings()
