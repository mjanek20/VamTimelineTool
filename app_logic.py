# app_logic.py
import os
import json
import copy
import math
from collections import defaultdict

from PyQt6.QtCore import QObject, pyqtSignal

from data_models import AnimationFile, AnimationClip, FloatParameter, ControllerTarget, TriggerGroup
from keyframe_logic import KeyframeEncoder, KeyframeDecoder
from enums import DropActionType

class MergeError(Exception):
    """Custom exception for merge failures."""
    pass

class AppLogic(QObject):
    file_changed = pyqtSignal(str)
    clips_updated = pyqtSignal()
    log_requested = pyqtSignal(str)
    error_occurred = pyqtSignal(str, str)

    def __init__(self):
        super().__init__()
        self.animation_file = None
        self.current_file_path = None
        self.last_center_root_delta_xz = (0.0, 0.0)

    def predict_drop_action(self, dragged_items_data, target_item_data, is_copy):
        """Predicts the outcome of a drop action without executing it."""
        if not dragged_items_data or not target_item_data:
            return DropActionType.INVALID, "No valid items."

        source_data = dragged_items_data[0]
        
        # --- Clip Drag Logic ---
        if isinstance(source_data, AnimationClip):
            source_clips = dragged_items_data
            
            # Determine target layer
            target_layer_data = None
            if isinstance(target_item_data, AnimationClip):
                target_layer_data = ('layer', target_item_data.atom_id, target_item_data.segment, target_item_data.layer)
            elif isinstance(target_item_data, tuple) and target_item_data[0] == 'layer':
                target_layer_data = target_item_data
            
            if not target_layer_data:
                return DropActionType.INVALID, "Clips can only be dropped on other clips or layers."

            src_atom, src_seg, src_layer = source_data.atom_id, source_data.segment, source_data.layer
            tgt_atom, tgt_seg, tgt_layer = target_layer_data[1], target_layer_data[2], target_layer_data[3]

            # Scenario 1: Reordering within the same layer
            if not is_copy and (src_atom, src_seg, src_layer) == (tgt_atom, tgt_seg, tgt_layer):
                return DropActionType.REORDER_CLIPS, "Reorder clips"

            # Scenario 2: Moving/Copying to another layer
            src_signature = self._get_layer_signature(src_atom, src_seg, src_layer, source_clips)
            
            # Check for empty target layer
            other_clips_in_target = [c for c in self.get_layer_clips(tgt_atom, tgt_seg, tgt_layer) if c not in source_clips]
            
            if not other_clips_in_target:
                action = DropActionType.COPY_CLIPS_COMPATIBLE if is_copy else DropActionType.MOVE_CLIPS_COMPATIBLE
                return action, f"{'Copy' if is_copy else 'Move'} to empty layer '{tgt_layer}'"

            tgt_signature = self._get_layer_signature(tgt_atom, tgt_seg, tgt_layer, other_clips_in_target)
            
            if src_signature == tgt_signature:
                action = DropActionType.COPY_CLIPS_COMPATIBLE if is_copy else DropActionType.MOVE_CLIPS_COMPATIBLE
                return action, f"{'Copy' if is_copy else 'Move'} to compatible layer '{tgt_layer}'"
            else:
                action = DropActionType.COPY_CLIPS_NEW_LAYER if is_copy else DropActionType.MOVE_CLIPS_NEW_LAYER
                verb = 'Copy' if is_copy else 'Move'
                return action, f"{verb} and create a new layer in '{tgt_seg}'"

        # --- Layer Drag Logic ---
        elif isinstance(source_data, tuple) and source_data[0] == 'layer':
            if not (isinstance(target_item_data, tuple) and target_item_data[0] == 'layer'):
                return DropActionType.INVALID, "Layers can only be dropped on other layers."
            
            src_atom, src_seg = source_data[1], source_data[2]
            tgt_atom, tgt_seg = target_item_data[1], target_item_data[2]
            
            if src_atom == tgt_atom and src_seg == tgt_seg:
                return DropActionType.MERGE_LAYERS, f"Merge '{source_data[3]}' into '{target_item_data[3]}'"
            else:
                return DropActionType.INVALID, "Layers can only be merged within the same segment."
        
        return DropActionType.INVALID, "Unknown drag operation."

    def load_file(self, file_name):
        try:
            with open(file_name, 'r', encoding='utf-8') as f: data = json.load(f)

            self.animation_file = AnimationFile()
            is_scene = "atoms" in data
            self.animation_file.is_scene = is_scene

            if is_scene:
                self.log_requested.emit("Loading scene file...")
                self.animation_file.original_json = data
                all_clips = []
                for atom_data in data.get("atoms", []):
                    atom_id = atom_data.get("id")
                    if not atom_id: continue
                    for storable_data in atom_data.get("storables", []):
                        storable_id = storable_data.get("id", "")
                        if "_VamTimeline.AtomPlugin" in storable_id:
                            anim_data = storable_data.get("Animation")
                            if anim_data and "Clips" in anim_data:
                                for i, clip_data in enumerate(anim_data.get("Clips", [])):
                                    clip = AnimationClip.from_dict(clip_data, atom_id=atom_id, storable_id=storable_id, order_index=i)
                                    all_clips.append(clip)
                self.animation_file.clips = all_clips
            else:
                self.log_requested.emit("Loading animation export file...")
                self.animation_file.version = data.get("SerializeVersion")
                self.animation_file.atom_type = data.get("AtomType")
                self.animation_file.clips = [
                    AnimationClip.from_dict(d, atom_id="(Standalone)", order_index=i)
                    for i, d in enumerate(data.get("Clips", []))
                ]
            
            self.current_file_path = file_name
            self.log_requested.emit(f"Loaded: {file_name}")
            self.file_changed.emit(file_name)

        except Exception as e:
            import traceback
            traceback.print_exc()
            self.animation_file = None
            self.current_file_path = None
            self.error_occurred.emit("Error Loading File", f"Failed to load '{file_name}':\n{e}")
            self.file_changed.emit(None)

    def mark_as_dirty(self):
        if self.current_file_path and not self.current_file_path.endswith(" *"):
            self.current_file_path += " *"
        elif not self.current_file_path:
             self.current_file_path = "Unsaved File *"

        self.clips_updated.emit()
    
    def get_layer_clips(self, atom_id, segment_name, layer_name):
        if not self.animation_file: return []
        is_scene = self.animation_file.is_scene
        return [c for c in self.animation_file.clips if (not is_scene or c.atom_id == atom_id) and c.segment == segment_name and c.layer == layer_name]

    def _get_layer_signature(self, atom_id, seg_name, layer_name, clips_source=None):
        """Calculates a 'signature' of a layer based on its controlled targets."""
        source = clips_source if clips_source is not None else self.animation_file.clips
        
        clips_in_layer = [
            c for c in source 
            if c.atom_id == atom_id and c.segment == seg_name and c.layer == layer_name
        ]

        if not clips_in_layer:
            return (frozenset(), frozenset(), frozenset())
            
        fp_keys = {(p.storable, p.name) for c in clips_in_layer for p in c.float_params}
        c_ids = {c.id for c in clips_in_layer for c in c.controllers}
        tg_names = {tg.name for c in clips_in_layer for tg in c.trigger_groups}
        return (frozenset(fp_keys), frozenset(c_ids), frozenset(tg_names))

    def merge_layers(self, src_layer_data, tgt_layer_data):
        src_atom_id, src_seg_name, src_layer_name = src_layer_data[1], src_layer_data[2], src_layer_data[3]
        tgt_atom_id, tgt_seg_name, tgt_layer_name = tgt_layer_data[1], tgt_layer_data[2], tgt_layer_data[3]

        if src_atom_id != tgt_atom_id or src_seg_name != tgt_seg_name:
            self.error_occurred.emit("Invalid Operation", "Layers can only be merged within the same segment of the same atom.")
            return

        self.log_requested.emit(f"Merging layer '{src_layer_name}' into '{tgt_layer_name}' in '{tgt_atom_id}/{tgt_seg_name}'.")

        src_clips = self.get_layer_clips(src_atom_id, src_seg_name, src_layer_name)
        tgt_clips = self.get_layer_clips(tgt_atom_id, tgt_seg_name, tgt_layer_name)

        all_clips = src_clips + tgt_clips
        master_fp = {(p.storable, p.name): p for clip in all_clips for p in clip.float_params}
        master_c = {c.id: c for clip in all_clips for c in clip.controllers}
        master_tg = {tg.name: tg for clip in all_clips for tg in clip.trigger_groups}

        for src_clip in src_clips:
            matching_tgt_clip = next((c for c in tgt_clips if c.name == src_clip.name), None)
            
            if matching_tgt_clip:
                existing_fp_keys = {(p.storable, p.name) for p in matching_tgt_clip.float_params}
                for param in src_clip.float_params:
                    if (param.storable, param.name) not in existing_fp_keys:
                        matching_tgt_clip.float_params.append(param)
                
                existing_c_ids = {c.id for c in matching_tgt_clip.controllers}
                for controller in src_clip.controllers:
                    if controller.id not in existing_c_ids:
                        matching_tgt_clip.controllers.append(controller)

                for src_tg in src_clip.trigger_groups:
                    current_tgt_tg_names = {tg.name for tg in matching_tgt_clip.trigger_groups}
                    if src_tg.name not in current_tgt_tg_names:
                        matching_tgt_clip.trigger_groups.append(src_tg)
                    else:
                        new_tg = copy.deepcopy(src_tg)
                        base_name, counter = new_tg.name, 1
                        new_name = f"{base_name} (merged)"
                        while new_name in current_tgt_tg_names:
                            counter += 1; new_name = f"{base_name} (merged {counter})"
                        self.log_requested.emit(f"Trigger group name conflict in clip '{matching_tgt_clip.name}'. Renaming '{base_name}' to '{new_name}'.")
                        new_tg.name = new_name
                        matching_tgt_clip.trigger_groups.append(new_tg)
                
                self.animation_file.clips.remove(src_clip)
            else:
                src_clip.layer = tgt_layer_name

        final_tgt_clips = self.get_layer_clips(tgt_atom_id, tgt_seg_name, tgt_layer_name)
        for clip in final_tgt_clips:
            clip_fp_keys = {(p.storable, p.name) for p in clip.float_params}
            for key, t_param in master_fp.items():
                if key not in clip_fp_keys:
                    new_param = FloatParameter(t_param.storable, t_param.name, [KeyframeEncoder.encode_keyframe(0.0, 0.0, 3, 0.0, -1), KeyframeEncoder.encode_keyframe(clip.length, 0.0, 3, 0.0, 3)], t_param.min, t_param.max)
                    clip.float_params.append(new_param)

            clip_c_ids = {c.id for c in clip.controllers}
            for c_id, t_ctrl in master_c.items():
                if c_id not in clip_c_ids:
                    new_c = ControllerTarget(c_id, **copy.deepcopy(t_ctrl.properties))
                    for axis in ['X', 'Y', 'Z', 'RotX', 'RotY', 'RotZ']:
                        new_c.properties[axis] = [KeyframeEncoder.encode_keyframe(0.0, 0.0, 3, 0.0, -1), KeyframeEncoder.encode_keyframe(clip.length, 0.0, 3, 0.0, 3)]
                    new_c.properties['RotW'] = [KeyframeEncoder.encode_keyframe(0.0, 1.0, 3, 0.0, -1), KeyframeEncoder.encode_keyframe(clip.length, 1.0, 3, 1.0, 3)]
                    clip.controllers.append(new_c)

            clip_tg_names = {tg.name for tg in clip.trigger_groups}
            for tg_name, t_group in master_tg.items():
                if tg_name not in clip_tg_names:
                    empty_triggers = [{"startTime": "0", "endTime": str(clip.length), "startActions": [], "transitionActions": [], "endActions": []}]
                    new_tg = TriggerGroup(name=tg_name, live=t_group.live, triggers=empty_triggers)
                    clip.trigger_groups.append(new_tg)

        self.log_requested.emit("Layer merge complete.")
        self.mark_as_dirty()

    def merge_animation_file(self, source_file_path, conflict_strategy):
        """Merges clips from another animation export file into the current one."""
        self.log_requested.emit(f"Starting merge from: {source_file_path}")
        
        # --- Pre-merge validation ---
        if not self.animation_file or self.animation_file.is_scene:
            raise MergeError("Cannot merge into a scene file or an empty project.")

        try:
            with open(source_file_path, 'r', encoding='utf-8') as f:
                source_data = json.load(f)
        except Exception as e:
            raise MergeError(f"Failed to read source file: {e}")

        if "atoms" in source_data:
            raise MergeError("Cannot merge a scene file. Only animation export files are supported.")
        
        source_anim = AnimationFile()
        source_anim.version = source_data.get("SerializeVersion")
        source_anim.atom_type = source_data.get("AtomType")
        source_anim.clips = [AnimationClip.from_dict(d, atom_id="(Standalone)", order_index=i) for i, d in enumerate(source_data.get("Clips", []))]
        
        if self.animation_file.atom_type != source_anim.atom_type:
            raise MergeError(f"Mismatched Atom Types.\nCurrent: {self.animation_file.atom_type}\nSource: {source_anim.atom_type}")

        self.log_requested.emit(f"Merge strategy for name conflicts: '{conflict_strategy}'")
        
        # --- Main merge logic ---
        source_grouped = defaultdict(lambda: defaultdict(list))
        for clip in source_anim.clips:
            source_grouped[clip.segment][clip.layer].append(clip)
        
        max_order = max((c.order_index for c in self.animation_file.clips), default=-1)
        added_count = 0

        for seg_name, layers in source_grouped.items():
            for layer_name, clips in layers.items():
                src_signature = self._get_layer_signature("(Standalone)", seg_name, layer_name, source_anim.clips)
                
                # Find compatible layer in target file
                target_layer_name = layer_name
                layers_in_target_segment = {c.layer for c in self.animation_file.clips if c.segment == seg_name}
                compatible_layer_found = False
                for existing_layer in layers_in_target_segment:
                    if src_signature == self._get_layer_signature("(Standalone)", seg_name, existing_layer):
                        target_layer_name = existing_layer
                        compatible_layer_found = True
                        break
                
                if not compatible_layer_found:
                    counter = 1
                    new_name = layer_name
                    while new_name in layers_in_target_segment:
                        new_name = f"{layer_name}_{counter}"; counter += 1
                    target_layer_name = new_name
                    self.log_requested.emit(f"Created new compatible layer '{target_layer_name}' in segment '{seg_name}'.")

                # Add clips to the determined target layer
                existing_names_in_tgt_layer = {c.name for c in self.animation_file.clips if c.segment == seg_name and c.layer == target_layer_name}
                for clip in clips:
                    is_conflict = clip.name in existing_names_in_tgt_layer
                    if is_conflict and conflict_strategy == "skip":
                        self.log_requested.emit(f"Skipping '{clip.name}' due to name conflict."); continue
                    
                    new_clip = copy.deepcopy(clip)
                    new_clip.segment, new_clip.layer = seg_name, target_layer_name
                    
                    if is_conflict and conflict_strategy == "replace":
                        to_remove = next(c for c in self.animation_file.clips if c.segment == seg_name and c.layer == target_layer_name and c.name == clip.name)
                        self.animation_file.clips.remove(to_remove)
                        self.log_requested.emit(f"Replacing clip '{clip.name}' in '{seg_name}/{target_layer_name}'.")
                    elif is_conflict and conflict_strategy == "rename":
                        base, i = clip.name, 1; new_name = f"{base}_merged"
                        while new_name in existing_names_in_tgt_layer: new_name = f"{base}_merged_{i}"; i += 1
                        new_clip.name = new_name
                        self.log_requested.emit(f"Renaming '{clip.name}' to '{new_clip.name}'.")
                    
                    max_order += 1
                    new_clip.order_index = max_order
                    self.animation_file.clips.append(new_clip)
                    existing_names_in_tgt_layer.add(new_clip.name)
                    added_count += 1
        
        self.log_requested.emit(f"Merge complete. Added {added_count} clip(s).")
        self.mark_as_dirty()


    def reorder_clips_in_layer(self, layer_data, dragged_clips_ids, target_clip_id, drop_pos):
        atom_id, seg_name, layer_name = layer_data[1], layer_data[2], layer_data[3]
        clips_in_layer = sorted(self.get_layer_clips(atom_id, seg_name, layer_name), key=lambda c: c.order_index)
        
        dragged_clips = [c for c in clips_in_layer if id(c) in dragged_clips_ids]
        remaining_clips = [c for c in clips_in_layer if id(c) not in dragged_clips_ids]
        
        target_clip = next((c for c in remaining_clips if id(c) == target_clip_id), None)
        target_idx = remaining_clips.index(target_clip) if target_clip else len(remaining_clips)
        
        if target_clip and drop_pos == 'Below':
            target_idx += 1
            
        for clip in reversed(dragged_clips):
            remaining_clips.insert(target_idx, clip)
            
        for i, clip in enumerate(remaining_clips):
            clip.order_index = i
            
        self.log_requested.emit(f"Reordered {len(dragged_clips)} clip(s) in layer '{layer_name}'.")
        self.mark_as_dirty()
        
    def move_or_copy_clips_to_layer(self, source_clips_ids, target_layer_data, is_copy):
        source_clips = [c for c in self.animation_file.clips if id(c) in source_clips_ids]
        if not source_clips: return

        src_sample = source_clips[0]
        src_atom, src_seg, src_layer = src_sample.atom_id, src_sample.segment, src_sample.layer
        tgt_atom, tgt_seg, tgt_layer_name = target_layer_data[1], target_layer_data[2], target_layer_data[3]

        final_tgt_layer_name = tgt_layer_name
        
        # Signature of the clips being moved.
        src_signature = self._get_layer_signature(src_atom, src_seg, src_layer, source_clips)

        # Signature of the target layer (excluding the clips that are about to be moved out of it, if it's the same layer).
        other_clips_in_target_layer = [
            c for c in self.get_layer_clips(tgt_atom, tgt_seg, tgt_layer_name) 
            if id(c) not in source_clips_ids
        ]
        
        # If the target layer will have other clips after the move, its signature must match the source clips' signature.
        if other_clips_in_target_layer:
            tgt_signature = self._get_layer_signature(tgt_atom, tgt_seg, tgt_layer_name, other_clips_in_target_layer)
            if src_signature != tgt_signature:
                # Signatures don't match. We must find an alternative home for the source clips.
                compatible_layer = None
                layers_in_tgt_segment = {c.layer for c in self.animation_file.clips if c.atom_id == tgt_atom and c.segment == tgt_seg}
                
                # Find a compatible layer in the target segment
                for existing_layer in layers_in_tgt_segment:
                    if self._get_layer_signature(tgt_atom, tgt_seg, existing_layer) == src_signature:
                        compatible_layer = existing_layer
                        self.log_requested.emit(f"Target layer '{tgt_layer_name}' is incompatible. Moving to compatible layer '{compatible_layer}'.")
                        break

                if compatible_layer:
                    final_tgt_layer_name = compatible_layer
                else:
                    # No compatible layer found, create a new one.
                    # Base the new name on the source layer's name to be intuitive.
                    new_layer_name = src_layer if not is_copy else tgt_layer_name
                    counter = 1
                    while new_layer_name in layers_in_tgt_segment:
                        # If moving within the same segment, use the source layer name as a base for the new name
                        base_name_for_new = src_layer if src_seg == tgt_seg else tgt_layer_name
                        new_layer_name = f"{base_name_for_new}_{counter}"
                        counter += 1
                    final_tgt_layer_name = new_layer_name
                    self.log_requested.emit(f"No compatible layer found. Creating new layer '{final_tgt_layer_name}' in '{tgt_seg}'.")

        clips_in_final_tgt = self.get_layer_clips(tgt_atom, tgt_seg, final_tgt_layer_name)
        max_order = max((c.order_index for c in clips_in_final_tgt), default=-1)

        for src_clip in source_clips:
            max_order += 1
            if is_copy:
                new_clip = copy.deepcopy(src_clip)
                new_clip.atom_id, new_clip.segment, new_clip.layer, new_clip.order_index = tgt_atom, tgt_seg, final_tgt_layer_name, max_order
                self.animation_file.clips.append(new_clip)
                self.log_requested.emit(f"Copied '{src_clip.name}' to '{tgt_atom}/{tgt_seg}/{final_tgt_layer_name}'.")
            else: # Move
                src_clip.atom_id, src_clip.segment, src_clip.layer, src_clip.order_index = tgt_atom, tgt_seg, final_tgt_layer_name, max_order
                self.log_requested.emit(f"Moved '{src_clip.name}' to '{tgt_atom}/{tgt_seg}/{final_tgt_layer_name}'.")
        
        self.mark_as_dirty()
        
    def delete_items(self, items_to_delete):
        segs, layers, clips_to_delete = set(), set(), set()
        for data in items_to_delete:
            if isinstance(data, tuple):
                if data[0] == 'segment': segs.add((data[1], data[2]))
                elif data[0] == 'layer': layers.add((data[1], data[2], data[3]))
            elif isinstance(data, AnimationClip):
                clips_to_delete.add(data)
        
        if not any([segs, layers, clips_to_delete]): return

        initial_count = len(self.animation_file.clips)
        self.animation_file.clips = [
            c for c in self.animation_file.clips if not (
                c in clips_to_delete or
                (c.atom_id, c.segment) in segs or
                (c.atom_id, c.segment, c.layer) in layers
            )
        ]
        deleted_count = initial_count - len(self.animation_file.clips)
        self.log_requested.emit(f"Deleted {deleted_count} clip(s).")
        self.mark_as_dirty()

    def _delete_targets_from_single_clip(self, clip_obj, targets_to_delete):
        """Internal helper to remove specified targets from a single clip object."""
        if not clip_obj or not targets_to_delete:
            return 0
        
        deleted_count = 0
        # Create sets of identifiers for faster lookup
        fps_to_del = {(t.storable, t.name) for t in targets_to_delete if isinstance(t, FloatParameter)}
        cts_to_del = {t.id for t in targets_to_delete if isinstance(t, ControllerTarget)}
        tgs_to_del = {t.name for t in targets_to_delete if isinstance(t, TriggerGroup)}

        initial_fp_count = len(clip_obj.float_params)
        clip_obj.float_params = [fp for fp in clip_obj.float_params if (fp.storable, fp.name) not in fps_to_del]
        deleted_count += initial_fp_count - len(clip_obj.float_params)
        
        initial_ct_count = len(clip_obj.controllers)
        clip_obj.controllers = [ct for ct in clip_obj.controllers if ct.id not in cts_to_del]
        deleted_count += initial_ct_count - len(clip_obj.controllers)

        initial_tg_count = len(clip_obj.trigger_groups)
        clip_obj.trigger_groups = [tg for tg in clip_obj.trigger_groups if tg.name not in tgs_to_del]
        deleted_count += initial_tg_count - len(clip_obj.trigger_groups)
        
        return deleted_count

    def process_target_deletion(self, source_clip, targets_to_delete, scope):
        """Handles the complex logic of deleting targets, ensuring layer integrity."""
        if not source_clip or not targets_to_delete:
            return

        if scope == 'layer':
            self.log_requested.emit(f"Starting layer-wide target deletion for layer '{source_clip.layer}'...")
            clips_in_layer = self.get_layer_clips(source_clip.atom_id, source_clip.segment, source_clip.layer)
            total_deleted = 0
            for clip in clips_in_layer:
                total_deleted += self._delete_targets_from_single_clip(clip, targets_to_delete)
            
            self.log_requested.emit(f"Finished: Deleted {total_deleted} target instances from {len(clips_in_layer)} clips.")
            self.mark_as_dirty()

        elif scope == 'move':
            self.log_requested.emit(f"Deleting targets from '{source_clip.name}' and moving to a compatible layer...")
            
            # Delete targets from the source clip first
            deleted_count = self._delete_targets_from_single_clip(source_clip, targets_to_delete)
            if deleted_count == 0:
                self.log_requested.emit("No matching targets found to delete. Operation cancelled.")
                return
            self.log_requested.emit(f"Deleted {deleted_count} target(s) from clip '{source_clip.name}'.")

            # Now, use the move logic to find a new home for the modified clip.
            # We tell it to "move" to its own layer, which will trigger the compatibility check.
            target_layer_data = ('layer', source_clip.atom_id, source_clip.segment, source_clip.layer)
            self.move_or_copy_clips_to_layer([id(source_clip)], target_layer_data, is_copy=False)
            
            # mark_as_dirty() is called inside move_or_copy_clips_to_layer

        else:
            self.log_requested.emit(f"Unknown deletion scope '{scope}'. Operation aborted.")

    def save_file(self, file_name):
        if not self.animation_file:
            self.log_requested.emit("Save cancelled: No data loaded.")
            return

        try:
            clean_path = file_name.replace(" *", "")
            if self.animation_file.is_scene:
                scene_json = copy.deepcopy(self.animation_file.original_json)
                for atom_data in scene_json.get("atoms", []):
                    for storable_data in atom_data.get("storables", []):
                        if "_VamTimeline.AtomPlugin" in storable_data.get("id", "") and "Animation" in storable_data:
                            storable_data["Animation"]["Clips"] = []
                
                grouped_clips = defaultdict(list)
                for clip in self.animation_file.clips:
                    grouped_clips[(clip.atom_id, clip.storable_id)].append(clip)
                
                for atom_data in scene_json.get("atoms", []):
                    atom_id = atom_data.get("id")
                    for storable_data in atom_data.get("storables", []):
                        storable_id = storable_data.get("id", "")
                        if "_VamTimeline.AtomPlugin" in storable_id:
                            key = (atom_id, storable_id)
                            clips_for_plugin = sorted(grouped_clips.get(key, []), key=lambda c: c.order_index)
                            if "Animation" in storable_data:
                                storable_data["Animation"]["Clips"] = [c.to_dict() for c in clips_for_plugin]
                
                output_data = scene_json
            else:
                output_data = self.animation_file.to_dict()

            with open(clean_path, 'w', encoding='utf-8') as f:
                json.dump(output_data, f, indent=3, ensure_ascii=False)
            
            self.current_file_path = clean_path
            self.log_requested.emit(f"File saved: {clean_path}")
            self.clips_updated.emit()

        except Exception as e:
            self.error_occurred.emit("Save Error", f"Save failed: {e}")

    def center_root_on_first_frame(self, clips_to_process):
        self.log_requested.emit(f"Starting 'Center Root (XZ only)' operation for {len(clips_to_process)} clip(s)...")
        if not clips_to_process: return

        clip = clips_to_process[0]
        root_options = ['control', 'hipControl', 'pelvisControl']
        root_controller = next((c for name in root_options for c in clip.controllers if c.id == name), None)

        if not root_controller:
            self.log_requested.emit(f"ERROR: Clip '{clip.name}' is missing a required root controller. Operation aborted.")
            return

        def get_pos_at_time(controller, axis, time_target=0.0):
            last_v, last_c = 0.0, 3
            for kf_str in controller.properties.get(axis, []):
                t, v, c = KeyframeDecoder.decode_keyframe(kf_str, last_v, last_c)
                if math.isclose(t, time_target, abs_tol=1e-5): return v
                last_v, last_c = v, c
            return 0.0

        p_root_local = [get_pos_at_time(root_controller, axis, 0.0) for axis in ['X', 'Y', 'Z']]
        delta = (-p_root_local[0], 0.0, -p_root_local[2])
        self.last_center_root_delta_xz = (delta[0], delta[2])
        self.log_requested.emit(f"Calculated XZ delta: ({delta[0]:.4f}, {delta[2]:.4f}). Applying to selected clips.")
        
        processed_count = self._apply_position_delta_to_clips(clips_to_process, delta)
        self.log_requested.emit(f"Root centering (XZ only) finished. Processed {processed_count} clip(s).")
        self.mark_as_dirty()

    def move_root_by_offset(self, clips_to_process, offsets):
        self.log_requested.emit(f"Applying manual offset {offsets} to {len(clips_to_process)} clip(s)...")
        processed_count = self._apply_position_delta_to_clips(clips_to_process, offsets)
        self.log_requested.emit(f"Manual offset operation finished. Processed {processed_count} clip(s).")
        self.mark_as_dirty()

    def create_new_segment(self, name):
        if not self.animation_file: return
        if any(c.segment == name for c in self.animation_file.clips):
            self.error_occurred.emit("Name Conflict", f"Segment '{name}' already exists.")
            return
        max_order = max((c.order_index for c in self.animation_file.clips), default=-1)
        atom_id = self.animation_file.clips[0].atom_id if self.animation_file.clips else "(Standalone)"
        new_clip = AnimationClip(name="New Animation", segment=name, layer="Main", length=1.0, order_index=max_order + 1, atom_id=atom_id)
        self.animation_file.clips.append(new_clip)
        self.log_requested.emit(f"Created segment '{name}'.")
        self.mark_as_dirty()

    def duplicate_clip(self, clip_obj):
        base, new_name = clip_obj.name, f"{clip_obj.name} (copy)"
        counter = 2
        existing_names = {c.name for c in self.animation_file.clips if c.atom_id == clip_obj.atom_id and c.segment == clip_obj.segment and c.layer == clip_obj.layer}
        while new_name in existing_names:
            new_name = f"{base} (copy {counter})"
            counter += 1
        
        new_clip = copy.deepcopy(clip_obj)
        new_clip.name = new_name
        new_clip.order_index = max((c.order_index for c in self.animation_file.clips), default=-1) + 1
        self.animation_file.clips.append(new_clip)
        
        self.log_requested.emit(f"Duplicated '{clip_obj.name}' as '{new_name}'.")
        self.mark_as_dirty()

    def batch_rename_clips(self, clips_to_rename, find, replace, prefix, suffix):
        renamed_count = 0
        for clip in clips_to_rename:
            original_name, new_name = clip.name, clip.name
            if find: new_name = new_name.replace(find, replace)
            if prefix: new_name = prefix + new_name
            if suffix: new_name = new_name + suffix
            
            if new_name != original_name:
                is_conflict = any(c.name == new_name and c.atom_id == clip.atom_id and c.layer == clip.layer and c.segment == clip.segment for c in self.animation_file.clips if c is not clip)
                if is_conflict:
                    self.log_requested.emit(f"SKIPPED rename for '{original_name}' due to name conflict.")
                    continue
                
                clip.name = new_name
                for other_clip in self.animation_file.clips:
                    if other_clip.other_properties.get("NextAnimationName") == original_name and other_clip.atom_id == clip.atom_id and other_clip.layer == clip.layer and other_clip.segment == clip.segment:
                        other_clip.other_properties["NextAnimationName"] = new_name
                renamed_count += 1
        
        if renamed_count > 0:
            self.log_requested.emit(f"Batch renamed {renamed_count} clip(s).")
            self.mark_as_dirty()

    def rename_item(self, data, new_name):
        if not self.animation_file or not new_name:
            self.clips_updated.emit()
            return

        if isinstance(data, AnimationClip):
            clip, old_name = data, data.name
            if new_name == old_name: return
            
            if any(c is not clip and c.name == new_name and c.layer == clip.layer and c.segment == clip.segment and c.atom_id == clip.atom_id for c in self.animation_file.clips):
                self.error_occurred.emit("Name Conflict", f"A clip named '{new_name}' already exists in this layer.")
                self.clips_updated.emit()
                return

            clip.name = new_name
            self.log_requested.emit(f"Renamed clip '{old_name}' to '{new_name}'.")
            
            for other_clip in self.animation_file.clips:
                if other_clip.other_properties.get("NextAnimationName") == old_name and other_clip.atom_id == clip.atom_id and other_clip.segment == clip.segment and other_clip.layer == clip.layer:
                    other_clip.other_properties["NextAnimationName"] = new_name
                    self.log_requested.emit(f"Updated NextAnimationName for '{other_clip.name}'.")
            self.mark_as_dirty()
        
        elif isinstance(data, tuple):
            item_type = data[0]
            if item_type == 'segment':
                atom_id, old_name = data[1], data[2]
                if new_name == old_name: return
                if any(c.segment == new_name and c.atom_id == atom_id for c in self.animation_file.clips):
                    self.error_occurred.emit("Name Conflict", f"Segment '{new_name}' already exists for this atom.")
                    self.clips_updated.emit()
                    return
                for clip in self.animation_file.clips:
                    if clip.atom_id == atom_id and clip.segment == old_name:
                        clip.segment = new_name
                self.log_requested.emit(f"Renamed segment '{old_name}' to '{new_name}'.")
                self.mark_as_dirty()
            elif item_type == 'layer':
                atom_id, seg_name, old_layer_name = data[1], data[2], data[3]
                if new_name == old_layer_name: return
                if any(c.layer == new_name and c.segment == seg_name and c.atom_id == atom_id for c in self.animation_file.clips):
                    self.error_occurred.emit("Name Conflict", f"Layer '{new_name}' already exists in this segment.")
                    self.clips_updated.emit()
                    return
                for clip in self.animation_file.clips:
                    if clip.atom_id == atom_id and clip.segment == seg_name and clip.layer == old_layer_name:
                        clip.layer = new_name
                self.log_requested.emit(f"Renamed layer '{old_layer_name}' to '{new_name}'.")
                self.mark_as_dirty()
    
    def _apply_position_delta_to_clips(self, clips, delta):
        processed_count = 0
        for clip in clips:
            try:
                for controller in clip.controllers:
                    if controller.id.endswith("Rotation"): continue
                    
                    for axis_idx, axis in enumerate(['X', 'Y', 'Z']):
                        if axis not in controller.properties: continue
                        
                        current_delta = delta[axis_idx]
                        if math.isclose(current_delta, 0.0, abs_tol=1e-6): continue

                        new_keyframes, last_v, last_c = [], 0.0, 3
                        
                        sorted_kfs = sorted(
                            [KeyframeDecoder.decode_keyframe(kf, 0.0, 3) for kf in controller.properties.get(axis, [])],
                            key=lambda k: k[0]
                        )
                        for t, v, c in sorted_kfs:
                            new_v = v + current_delta
                            new_kf_str = KeyframeEncoder.encode_keyframe(t, new_v, c, last_v, last_c)
                            new_keyframes.append(new_kf_str)
                            last_v, last_c = new_v, c
                        controller.properties[axis] = new_keyframes
                processed_count += 1
            except Exception as e:
                self.log_requested.emit(f"ERROR: Failed to process clip '{clip.name}'. Reason: {e}")
        return processed_count