def get_active_household():
    import services

    return services.active_household()


def iter_active_household_sim_infos():
    import services

    hh = services.active_household()
    if hh is None:
        return []
    gen = None
    if hasattr(hh, "sim_info_gen") and callable(hh.sim_info_gen):
        gen = hh.sim_info_gen()
    else:
        try:
            gen = list(hh)
        except Exception:
            gen = None
    if gen is None:
        return []
    return [sim_info for sim_info in gen if sim_info is not None]


def _sim_info_is_selectable(sim_info):
    if sim_info is None:
        return False
    attr = getattr(sim_info, "is_selectable", None)
    if attr is not None:
        try:
            return bool(attr() if callable(attr) else attr)
        except Exception:
            return False
    attr = getattr(sim_info, "is_played", None)
    if attr is not None:
        try:
            return bool(attr() if callable(attr) else attr)
        except Exception:
            return False
    is_npc = getattr(sim_info, "is_npc", None)
    if is_npc is not None:
        try:
            return not bool(is_npc() if callable(is_npc) else is_npc)
        except Exception:
            return False
    return False


def iter_playable_household_sim_infos():
    return iter_active_household_sim_infos() or []


def is_active_household_sim(sim_info):
    import services

    hh = services.active_household()
    if hh is None or sim_info is None:
        return False
    try:
        return sim_info.household is hh
    except Exception:
        return False
