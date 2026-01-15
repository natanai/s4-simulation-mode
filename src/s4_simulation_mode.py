try:
    import simulation_mode  # triggers simulation_mode/__init__.py which imports commands for registration
except Exception as e:
    import sims4.log

    logger = sims4.log.Logger('S4SimulationMode')
    logger.exception('Failed to import simulation_mode package', exc=e)
