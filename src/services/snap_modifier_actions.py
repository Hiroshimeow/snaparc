from dataclasses import dataclass


ACTION_DEFAULT = "default_capture"
ACTION_CUSTOM_PROMPT = "custom_prompt"
ACTION_CUSTOM_PROMPT_NEW_SESSION = "custom_prompt_new_session"
ACTION_TEXT_FILE_PROMPT = "text_file_prompt"
ACTION_RADIAL_WHEEL = "radial_wheel"


@dataclass(frozen=True)
class SnapModifiers:
    ctrl: bool = False
    shift: bool = False
    alt: bool = False

    @classmethod
    def from_mapping(cls, mapping):
        if not isinstance(mapping, dict):
            return cls()
        return cls(
            ctrl=bool(mapping.get("ctrl", False)),
            shift=bool(mapping.get("shift", False)),
            alt=bool(mapping.get("alt", False)),
        )

    def active_names(self):
        names = []
        if self.ctrl:
            names.append("ctrl")
        if self.shift:
            names.append("shift")
        if self.alt:
            names.append("alt")
        return tuple(names)

    def contains(self, required_names):
        active = set(self.active_names())
        return set(required_names).issubset(active)


@dataclass(frozen=True)
class SnapAction:
    name: str
    modifiers: SnapModifiers
    keep_force_new_window: bool = True


class SnapActionRouter:
    """
    Resolve mouse gestures and keyboard modifiers to STC v1.1 actions:
    - Ctrl + Alt: Radial Plugin Wheel (`ACTION_RADIAL_WHEEL`)
    - Ctrl + Shift: Send snapped images directly to Pi Chat Screen (`ACTION_CUSTOM_PROMPT_NEW_SESSION`)
    - Ctrl: Open Unified Quick Prompt Popup in Image mode (`ACTION_CUSTOM_PROMPT`)
    - Alt: Open Unified Quick Prompt Popup in Text/File Q&A mode (`ACTION_TEXT_FILE_PROMPT`)
    - Shift / None: Default capture (stacking if Shift is held) (`ACTION_DEFAULT`)
    """

    def __init__(self, rules=None):
        self.rules = tuple(rules or self.default_rules())

    @classmethod
    def from_config(cls, config):
        rules = []
        raw_rules = config if isinstance(config, list) else []
        for item in raw_rules:
            if not isinstance(item, dict):
                continue
            action_name = str(item.get("action") or item.get("name") or "").strip()
            modifier_names = item.get("modifiers", [])
            if not action_name or not isinstance(modifier_names, list):
                continue
            # Migrate any obsolete v2.0 compare_or_pin config to text_file_prompt
            if action_name == "compare_or_pin":
                action_name = ACTION_TEXT_FILE_PROMPT
            modifiers = cls._modifiers_from_names(modifier_names)
            rules.append(
                SnapAction(
                    name=action_name,
                    modifiers=modifiers,
                    keep_force_new_window=bool(item.get("keep_force_new_window", True)),
                )
            )
        return cls(rules=rules or cls.default_rules())

    @staticmethod
    def default_rules():
        return (
            SnapAction(
                name=ACTION_RADIAL_WHEEL,
                modifiers=SnapModifiers(ctrl=True, alt=True),
            ),
            SnapAction(
                name=ACTION_CUSTOM_PROMPT_NEW_SESSION,
                modifiers=SnapModifiers(ctrl=True, shift=True),
            ),
            SnapAction(
                name=ACTION_CUSTOM_PROMPT,
                modifiers=SnapModifiers(ctrl=True),
            ),
            SnapAction(
                name=ACTION_TEXT_FILE_PROMPT,
                modifiers=SnapModifiers(alt=True),
            ),
        )

    @staticmethod
    def _modifiers_from_names(names):
        normalized = {str(name).strip().lower() for name in names}
        return SnapModifiers(
            ctrl="ctrl" in normalized or "control" in normalized,
            shift="shift" in normalized,
            alt="alt" in normalized,
        )

    def resolve(self, modifiers):
        snap_modifiers = SnapModifiers.from_mapping(modifiers)
        best_rule = None
        best_specificity = -1
        for rule in self.rules:
            required = rule.modifiers.active_names()
            if not required:
                continue
            if snap_modifiers.contains(required) and len(required) > best_specificity:
                best_rule = rule
                best_specificity = len(required)
        if best_rule is not None:
            return best_rule
        return SnapAction(name=ACTION_DEFAULT, modifiers=snap_modifiers)
