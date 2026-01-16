def get_active_household():
    import services

    return services.active_household()


def iter_active_household_sim_infos():
    import services

    hh = services.active_household()
    if hh is None:
        return
    gen = None
    if hasattr(hh, "sim_info_gen") and callable(hh.sim_info_gen):
        gen = hh.sim_info_gen()
    elif hasattr(hh, "sim_infos"):
        gen = getattr(hh, "sim_infos", None)
    if gen is None:
        return
    for sim_info in gen:
        if sim_info is None:
            continue
        if hasattr(sim_info, "is_human") and callable(sim_info.is_human):
            try:
                if not sim_info.is_human():
                    continue
            except Exception:
                pass
        yield sim_info


def is_active_household_sim(sim_info):
    import services

    hh = services.active_household()
    if hh is None or sim_info is None:
        return False
    try:
        return sim_info.household is hh
    except Exception:
        return False
