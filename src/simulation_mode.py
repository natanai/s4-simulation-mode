import sims4.commands
from sims4.commands import CommandType, BOOL_TRUE

SIMULATION_MODE_ENABLED = False

_FALSE_STRINGS = {"false", "f", "0", "off", "no", "n"}


def _parse_bool(arg: str):
    if arg is None:
        return None
    s = arg.strip().lower()
    if s in BOOL_TRUE:
        return True
    if s in _FALSE_STRINGS:
        return False
    return None


@sims4.commands.Command('simulation', command_type=CommandType.Live)
def simulation_cmd(enable: str = None, _connection=None):
    global SIMULATION_MODE_ENABLED
    output = sims4.commands.CheatOutput(_connection)

    parsed = _parse_bool(enable)
    if parsed is not None:
        SIMULATION_MODE_ENABLED = parsed

    output(f"Simulation Mode = {SIMULATION_MODE_ENABLED}")
    return True


@sims4.commands.Command('simulation_mode', command_type=CommandType.Live)
def simulation_mode_cmd(enable: str = None, _connection=None):
    return simulation_cmd(enable, _connection)
