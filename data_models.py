# data_models.py
from collections import defaultdict
import copy
from keyframe_logic import KeyframeEncoder # Ważny import!

class FloatParameter:
    def __init__(self, storable, name, value, min_val, max_val):
        self.storable, self.name, self.value, self.min, self.max = storable, name, value, min_val, max_val
    @classmethod
    def from_dict(cls, data): return cls(data.get("Storable"), data.get("Name"), data.get("Value", []), data.get("Min"), data.get("Max"))
    def to_dict(self):
        props = {"Storable": self.storable, "Name": self.name, "Value": self.value}
        if self.min is not None: props["Min"] = self.min
        if self.max is not None: props["Max"] = self.max
        return props

class ControllerTarget:
    def __init__(self, controller_id, **kwargs):
        self.id, self.properties = controller_id, kwargs
        for key in ['X', 'Y', 'Z', 'RotX', 'RotY', 'RotZ', 'RotW']:
            if key not in self.properties: self.properties[key] = []
    @classmethod
    def from_dict(cls, data):
        controller_id = data.get("Controller")
        return cls(controller_id, **{k: v for k, v in data.items() if k != "Controller"})
    def to_dict(self):
        data = {"Controller": self.id}; data.update(self.properties); return data

class TriggerGroup:
    """Represents a named group of triggers, like 'Audio 1' or 'Triggers 1'."""
    def __init__(self, name, live, triggers):
        self.name = name
        self.live = live
        self.triggers = triggers

    @classmethod
    def from_dict(cls, data):
        return cls(
            name=data.get("Name", "Unnamed Trigger"),
            live=data.get("Live", "0"),
            triggers=data.get("Triggers", [])
        )
    
    def to_dict(self):
        return {
            "Name": self.name,
            "Live": self.live,
            "Triggers": self.triggers
        }

class AnimationClip:
    def __init__(self, name, segment, layer, length, order_index=0, atom_id=None, storable_id=None, **kwargs):
        self.name = name
        self.segment = segment
        self.layer = layer
        self.length = length
        self.order_index = order_index
        self.atom_id = atom_id
        self.storable_id = storable_id
        self.other_properties = kwargs
        self.float_params = []
        self.controllers = []
        self.trigger_groups = []

    @classmethod
    def from_dict(cls, data, atom_id=None, storable_id=None, order_index=0):
        """
        Creates an AnimationClip from a dictionary.
        Accepts order_index as a direct keyword argument.
        """
        known_keys = {"AnimationName", "AnimationSegment", "AnimationLayer", "AnimationLength", "FloatParams", "Controllers", "Triggers", "OrderIndex"}
        instance = cls(
            name=data.get("AnimationName", "Unnamed"),
            segment=data.get("AnimationSegment", "Default"),
            layer=data.get("AnimationLayer", "Default"),
            length=float(data.get("AnimationLength", 0.0)),
            order_index=order_index,  # Use the passed argument directly
            atom_id=atom_id,
            storable_id=storable_id,
            **{k: v for k, v in data.items() if k not in known_keys}
        )
        if "FloatParams" in data:
            instance.float_params = [FloatParameter.from_dict(p) for p in data["FloatParams"]]
        if "Controllers" in data:
            instance.controllers = [ControllerTarget.from_dict(c) for c in data["Controllers"]]
        if "Triggers" in data:
            instance.trigger_groups = [TriggerGroup.from_dict(tg) for tg in data["Triggers"]]
        return instance

    def to_dict(self):
        data = {
            "AnimationName": self.name,
            "AnimationSegment": self.segment,
            "AnimationLayer": self.layer,
            "AnimationLength": str(self.length)
        }
        data.update(self.other_properties)
        if self.float_params: data["FloatParams"] = [p.to_dict() for p in sorted(self.float_params, key=lambda p: (p.storable, p.name))]
        if self.controllers: data["Controllers"] = [c.to_dict() for c in sorted(self.controllers, key=lambda c: c.id)]
        if self.trigger_groups: data["Triggers"] = [tg.to_dict() for tg in sorted(self.trigger_groups, key=lambda tg: tg.name)]
        return data

class AnimationFile:
    def __init__(self):
        self.version = None
        self.atom_type = None
        self.clips = []
        self.is_scene = False
        self.original_json = None
    
    def to_dict(self):
        if self.is_scene:
            raise NotImplementedError("to_dict is not for scene files, handle separately")
        return {
            "SerializeVersion": self.version,
            "AtomType": self.atom_type,
            "Clips": [c.to_dict() for c in sorted(self.clips, key=lambda c: c.order_index)]
        }