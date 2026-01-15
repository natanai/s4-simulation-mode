import importlib
import importlib.util
import inspect

from interactions.context import (
    InteractionContext,
    QueueInsertStrategy,
    InteractionBucketType,
    InteractionSource,
)
import interactions.priority as priority
import services

_MOOD_LOCK_TOKENS = (
    "energetic",
    "focused",
    "confident",
    "inspired",
    "playful",
    "flirty",
    "angry",
    "sad",
    "tense",
    "uncomfortable",
    "scared",
    "dazed",
)


_PICKER_SUPER_INTERACTION = None
_PICKER_SPEC = importlib.util.find_spec("interactions.base.picker_interaction")
if _PICKER_SPEC is not None:
    _picker_module = importlib.import_module("interactions.base.picker_interaction")
    _PICKER_SUPER_INTERACTION = getattr(_picker_module, "PickerSuperInteraction", None)

_UNSAFE_AFFORDANCE_CACHE = set()


def is_picker_affordance(affordance):
    if affordance is None:
        return False
    if _PICKER_SUPER_INTERACTION is not None:
        try:
            if inspect.isclass(affordance) and issubclass(
                affordance, _PICKER_SUPER_INTERACTION
            ):
                return True
        except Exception:
            pass
    if hasattr(affordance, "picker_dialog"):
        return True
    name = (
        getattr(affordance, "__name__", None)
        or getattr(affordance, "__qualname__", None)
        or ""
    )
    return "picker" in name.lower()


def get_first_client():
    try:
        client_manager = services.client_manager()
        if client_manager is None:
            return None
        getter = getattr(client_manager, "get_first_client", None)
        if callable(getter):
            return getter()
    except Exception:
        return None
    return None


def _resolve_source(source, force):
    if source is not None:
        return source
    try:
        return InteractionSource.PIE_MENU if force else InteractionSource.AUTONOMY
    except Exception:
        try:
            return (
                InteractionContext.SOURCE_PIE_MENU
                if force
                else InteractionContext.SOURCE_AUTONOMY
            )
        except Exception:
            return (
                InteractionContext.SOURCE_PIE_MENU
                if force
                else InteractionContext.SOURCE_AUTONOMY
            )


def make_interaction_context(sim, *, force=False, source=None):
    src = _resolve_source(source, force)
    prio = priority.Priority.Critical if force else priority.Priority.High

    try:
        insert = QueueInsertStrategy.FIRST if force else QueueInsertStrategy.NEXT
    except Exception:
        insert = QueueInsertStrategy.NEXT

    # IMPORTANT: match build-29 takeover behavior.
    # Forced pushes must use DEFAULT bucket when available.
    bucket = None
    try:
        bucket = InteractionBucketType.DEFAULT
    except Exception:
        bucket = None

    kwargs = {"insert_strategy": insert}
    if bucket is not None:
        kwargs["bucket"] = bucket

    client = get_first_client()
    if client is not None:
        try:
            return InteractionContext(sim, src, prio, client=client, **kwargs), True
        except TypeError:
            try:
                kwargs.pop("bucket", None)
                return InteractionContext(sim, src, prio, client=client, **kwargs), True
            except TypeError:
                pass

    try:
        return InteractionContext(sim, src, prio, **kwargs), False
    except TypeError:
        try:
            kwargs.pop("bucket", None)
            return InteractionContext(sim, src, prio, **kwargs), False
        except Exception:
            return InteractionContext(sim, src, prio), False


def _iter_objects_from_manager(object_manager):
    if object_manager is None:
        return []

    for attr in ("objects", "_objects"):
        mapping = getattr(object_manager, attr, None)
        if mapping is None:
            continue
        try:
            values = getattr(mapping, "values", None)
            if callable(values):
                return list(values())
        except Exception:
            pass

    try:
        values = getattr(object_manager, "values", None)
        if callable(values):
            return list(values())
    except Exception:
        pass

    get_objects = getattr(object_manager, "get_objects", None)
    if callable(get_objects):
        try:
            sig = inspect.signature(get_objects)
            required = [
                p
                for p in sig.parameters.values()
                if p.default is inspect._empty
                and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
            ]
            if len(required) == 0:
                return list(get_objects())
        except Exception:
            try:
                return list(get_objects())
            except TypeError:
                pass
            except Exception:
                pass

    try:
        return list(iter(object_manager))
    except Exception:
        return []


def iter_objects():
    try:
        object_manager = services.object_manager()
    except Exception:
        object_manager = None

    objs = _iter_objects_from_manager(object_manager)
    if objs:
        return objs

    try:
        zone = services.current_zone()
        zone_object_manager = getattr(zone, "object_manager", None)
    except Exception:
        zone_object_manager = None

    if zone_object_manager and zone_object_manager is not object_manager:
        objs = _iter_objects_from_manager(zone_object_manager)
        if objs:
            return objs

    return []


def iter_super_affordances(obj, sim=None):
    result = []
    affordances = getattr(obj, "super_affordances", None)
    if callable(affordances):
        try:
            affordances = affordances()
        except Exception:
            affordances = None
    if affordances:
        result.extend(affordances)

    affordances = getattr(obj, "_super_affordances", None)
    if callable(affordances):
        try:
            affordances = affordances()
        except Exception:
            affordances = None
    if affordances:
        result.extend(affordances)

    getter = getattr(obj, "get_super_affordances", None)
    if callable(getter):
        try:
            result.extend(getter())
        except Exception:
            pass

    target_getter = getattr(obj, "get_target_super_affordances", None)
    if callable(target_getter) and sim is not None:
        try:
            result.extend(target_getter(sim))
        except Exception:
            pass

    return list(dict.fromkeys(result))


def affordance_name(aff):
    return (
        getattr(aff, "__name__", None)
        or getattr(aff, "__qualname__", None)
        or str(aff)
    ).lower()


def _required_kwonly_args(aff):
    init = getattr(aff, "__init__", None)
    if init is None:
        return []
    try:
        sig = inspect.signature(init)
    except Exception:
        return []
    required = [
        param.name.lower()
        for param in sig.parameters.values()
        if param.kind == param.KEYWORD_ONLY and param.default is inspect._empty
    ]
    return required


def is_safe_for_script_push(aff):
    if aff is None:
        return False, "none"
    name = affordance_name(aff)
    if name in _UNSAFE_AFFORDANCE_CACHE:
        return False, "cached_unsafe"
    if is_picker_affordance(aff):
        return False, "picker_affordance"
    if any(fragment in name for fragment in ("createfood", "createfinalfood")):
        return False, "crafting_food_affordance"
    required = _required_kwonly_args(aff)
    if required:
        return False, "requires_kwonly=" + ",".join(required)
    return True, "ok"


def _score_affordance(affordance, keywords):
    name = affordance_name(affordance)
    score = 0
    if any(token in name for token in _MOOD_LOCK_TOKENS):
        score -= 20
    for keyword in keywords:
        if keyword and keyword.lower() in name:
            score += 10
    if "workout" in name:
        score += 5
    return score


def find_affordance_candidates(obj, keywords, sim=None, debug_append=None):
    if not keywords:
        return []
    affordances = iter_super_affordances(obj, sim)
    if not affordances:
        return []
    candidates = []
    lowered_keywords = [keyword.lower() for keyword in keywords if keyword]
    for affordance in affordances:
        try:
            name = affordance_name(affordance)
            if any(keyword in name for keyword in lowered_keywords):
                safe, reason = is_safe_for_script_push(affordance)
                if not safe:
                    if debug_append:
                        debug_append(
                            f"SKIP unsafe affordance {name} reason={reason}"
                        )
                    continue
                candidates.append(affordance)
        except Exception:
            continue
    candidates.sort(
        key=lambda affordance: _score_affordance(affordance, lowered_keywords), reverse=True
    )
    return candidates


def call_push_super_affordance(sim, super_affordance, target, context):
    fn = getattr(sim, "push_super_affordance", None)
    if fn is None:
        return False, "no push_super_affordance on sim", []

    safe, reason = is_safe_for_script_push(super_affordance)
    if not safe:
        return False, f"unsafe affordance: {reason}", []

    try:
        sig = inspect.signature(fn)
        params = list(sig.parameters.values())

        if params and params[0].name == "self":
            params = params[1:]

        names = [p.name for p in params]
        kwargs = {}

        if "context" in names:
            kwargs["context"] = context
        elif "interaction_context" in names:
            kwargs["interaction_context"] = context

        if "picked_item_ids" in names:
            kwargs["picked_item_ids"] = None

        result = fn(super_affordance, target, **kwargs)
        if not bool(result):
            return (
                False,
                "push_super_affordance returned False",
                names,
            )
        return True, None, names
    except Exception as exc:
        param_names = names if "names" in locals() else []
        message = str(exc)
        lower_message = message.lower()
        if "crafting_process" in lower_message or "phase" in lower_message:
            _UNSAFE_AFFORDANCE_CACHE.add(affordance_name(super_affordance))
        return False, f"exception calling push_super_affordance: {exc!r}", param_names


def push_best_affordance(
    sim,
    target_obj,
    keywords,
    *,
    force=False,
    source=None,
    max_candidates=8,
    debug_append=None,
):
    context, client_attached = make_interaction_context(sim, force=force, source=source)
    candidates = find_affordance_candidates(
        target_obj, keywords, sim=sim, debug_append=debug_append
    )
    if not candidates:
        return False, None, "no safe affordances"
    for affordance in candidates[:max_candidates]:
        safe, reason = is_safe_for_script_push(affordance)
        if not safe:
            if debug_append:
                debug_append(
                    "SKIP unsafe affordance {} reason={}".format(
                        affordance_name(affordance), reason
                    )
                )
            continue
        ok, reason, sig_names = call_push_super_affordance(
            sim, affordance, target_obj, context
        )
        if ok:
            return True, affordance_name(affordance), None
        if debug_append:
            debug_append(
                "candidate failed aff={} reason={} sig_names={} client_attached={}".format(
                    affordance_name(affordance),
                    reason or "unknown",
                    sig_names,
                    client_attached,
                )
            )
    return False, None, "all safe candidates failed"


def find_objects_by_definition_id(def_id: int):
    if def_id is None:
        return []
    results = []
    for obj in iter_objects():
        if getattr(obj, "is_sim", False):
            continue
        definition = getattr(obj, "definition", None)
        if definition is None:
            continue
        try:
            if getattr(definition, "id", None) == def_id:
                results.append(obj)
        except Exception:
            continue
    return results


def resolve_affordance_by_guid(obj, aff_guid64: int):
    if obj is None or aff_guid64 is None:
        return None
    for affordance in iter_super_affordances(obj):
        try:
            guid = getattr(affordance, "guid64", None)
        except Exception:
            guid = None
        if guid == aff_guid64:
            return affordance
    return None


def _distance(sim, obj):
    sim_pos = getattr(sim, "position", None)
    obj_pos = getattr(obj, "position", None)
    if sim_pos is None or obj_pos is None:
        return None
    if all(hasattr(sim_pos, axis) for axis in ("x", "y", "z")) and all(
        hasattr(obj_pos, axis) for axis in ("x", "y", "z")
    ):
        try:
            dx = sim_pos.x - obj_pos.x
            dy = sim_pos.y - obj_pos.y
            dz = sim_pos.z - obj_pos.z
            return (dx * dx + dy * dy + dz * dz) ** 0.5
        except Exception:
            return None
    try:
        delta = sim_pos - obj_pos
        magnitude = getattr(delta, "magnitude", None)
        return magnitude() if callable(magnitude) else magnitude
    except Exception:
        return None


def push_by_def_and_aff_guid(sim, def_id: int, aff_guid64: int, reason: str, probe_details=None):
    if sim is None or def_id is None or aff_guid64 is None:
        return False
    objects = find_objects_by_definition_id(def_id)
    if not objects:
        if probe_details is not None:
            probe_details.setdefault("push_attempts", []).append(
                {"obj_def_id": def_id, "aff_guid64": aff_guid64, "result": "no_objects"}
            )
        return False
    sorted_objects = []
    for obj in objects:
        distance = _distance(sim, obj)
        sorted_objects.append((distance if distance is not None else float("inf"), obj))
    sorted_objects.sort(key=lambda item: item[0])
    context, _client_attached = make_interaction_context(sim, force=False)
    for _distance_value, obj in sorted_objects:
        aff = resolve_affordance_by_guid(obj, aff_guid64)
        if aff is None:
            if probe_details is not None:
                probe_details.setdefault("push_attempts", []).append(
                    {
                        "obj_def_id": def_id,
                        "aff_guid64": aff_guid64,
                        "result": "aff_not_found",
                    }
                )
            continue
        ok, failure_reason, _sig_names = call_push_super_affordance(
            sim, aff, obj, context
        )
        if probe_details is not None:
            probe_details.setdefault("push_attempts", []).append(
                {
                    "obj_def_id": def_id,
                    "aff_guid64": aff_guid64,
                    "result": "ok" if ok else "push_failed",
                    "failure_reason": failure_reason,
                    "reason": reason,
                }
            )
        if ok:
            return True
    return False
