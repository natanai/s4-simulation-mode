try:
    import simulation_mode  # triggers __init__.py where commands are registered
except Exception as e:
    import sims4.log

    logger = sims4.log.Logger('S4SimulationMode')
    logger.exception('Failed to import simulation_mode package', exc=e)
