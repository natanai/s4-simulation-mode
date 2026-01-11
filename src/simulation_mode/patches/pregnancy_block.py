from simulation_mode.settings import settings


def apply_patch():
    try:
        from sims.pregnancy.pregnancy_tracker import PregnancyTracker
    except Exception:
        return False

    try:
        original = PregnancyTracker.start_pregnancy
    except Exception:
        return False

    if getattr(PregnancyTracker.start_pregnancy, "_simulation_mode_patched", False):
        return True

    def wrapper(self, *args, **kwargs):
        try:
            if settings.enabled and not settings.allow_pregnancy:
                return False
        except Exception:
            return original(self, *args, **kwargs)
        return original(self, *args, **kwargs)

    wrapper._simulation_mode_patched = True
    try:
        PregnancyTracker.start_pregnancy = wrapper
    except Exception:
        return False
    return True
