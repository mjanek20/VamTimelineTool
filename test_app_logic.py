# test_app_logic.py
import pytest
import os
import json
import copy

# Upewnij się, że ten plik jest w tym samym katalogu co inne moduły
from app_logic import AppLogic, MergeError
from data_models import AnimationFile, AnimationClip, ControllerTarget, FloatParameter, TriggerGroup
from keyframe_logic import KeyframeEncoder, KeyframeDecoder
from enums import DropActionType
from main import MainWindow # Import MainWindow dla testów UI

# --- Fixtures: Dane testowe i obiekty pomocnicze ---

@pytest.fixture
def app_logic_instance():
    """Zwraca nową, czystą instancję AppLogic dla każdego testu."""
    return AppLogic()

@pytest.fixture
def sample_animation_file_data():
    """Zwraca słownik reprezentujący prosty plik eksportu animacji."""
    return {
        "SerializeVersion": "4",
        "AtomType": "Person",
        "Clips": [
            {
                "AnimationName": "Clip A",
                "AnimationSegment": "Segment 1",
                "AnimationLayer": "Main",
                "AnimationLength": "2.5",
                "Controllers": [{"Controller": "hipControl", "X": []}]
            },
            {
                "AnimationName": "Clip B",
                "AnimationSegment": "Segment 1",
                "AnimationLayer": "Main",
                "AnimationLength": "3.0"
            },
            {
                "AnimationName": "Clip C",
                "AnimationSegment": "Segment 2",
                "AnimationLayer": "Secondary",
                "AnimationLength": "1.0"
            }
        ]
    }

@pytest.fixture
def sample_scene_file_data():
    """Zwraca słownik reprezentujący prosty plik sceny (.json)."""
    return {
        "atoms": [
            {
                "id": "Person",
                "storables": [
                    { "id": "geometry" },
                    {
                        "id": "Plugin#1_VamTimeline.AtomPlugin",
                        "Animation": {
                            "Clips": [
                                {
                                    "AnimationName": "Scene Clip 1",
                                    "AnimationSegment": "Scene Seg 1",
                                    "AnimationLayer": "Base",
                                    "AnimationLength": "5.0"
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }

@pytest.fixture
def temp_json_file(tmp_path):
    """Fixture do tworzenia tymczasowych plików JSON z danymi."""
    def _creator(file_name, data):
        path = tmp_path / file_name
        with open(path, 'w') as f:
            json.dump(data, f)
        return str(path)
    return _creator


# --- Testy dla Keyframe Logic (podstawa działania) ---

class TestKeyframeLogic:
    def test_encoding_decoding_cycle(self):
        """Sprawdza, czy zakodowany i zdekodowany klucz klatkowy daje te same wartości."""
        time, value, curve_type = 1.25, -10.5, 3
        last_v, last_c = 0.0, 0
        encoded = KeyframeEncoder.encode_keyframe(time, value, curve_type, last_v, last_c)
        decoded_time, decoded_value, decoded_curve_type = KeyframeDecoder.decode_keyframe(encoded, last_v, last_c)
        assert abs(decoded_time - time) < 1e-6
        assert abs(decoded_value - value) < 1e-6
        assert decoded_curve_type == curve_type
        
    def test_encoding_no_change(self):
        """Sprawdza, czy kodowanie bez zmiany wartości/typu krzywej generuje krótszy ciąg."""
        encoded = KeyframeEncoder.encode_keyframe(2.0, 10.0, 1, 10.0, 1)
        assert len(encoded) == 9
        assert encoded.startswith("A")

# --- Testy dla Głównej Logiki Aplikacji ---

class TestAppLogic:
    def test_load_animation_file(self, app_logic_instance, temp_json_file, sample_animation_file_data):
        path = temp_json_file("test.json", sample_animation_file_data)
        app_logic_instance.load_file(path)
        assert app_logic_instance.animation_file is not None
        assert not app_logic_instance.animation_file.is_scene
        assert len(app_logic_instance.animation_file.clips) == 3

    def test_load_scene_file(self, app_logic_instance, temp_json_file, sample_scene_file_data):
        path = temp_json_file("scene.json", sample_scene_file_data)
        app_logic_instance.load_file(path)
        assert app_logic_instance.animation_file is not None
        assert app_logic_instance.animation_file.is_scene
        
    def test_mark_as_dirty(self, app_logic_instance):
        app_logic_instance.current_file_path = "test.json"
        app_logic_instance.mark_as_dirty()
        assert app_logic_instance.current_file_path == "test.json *"
        
    def test_delete_items(self, app_logic_instance, temp_json_file, sample_animation_file_data):
        path = temp_json_file("test.json", sample_animation_file_data)
        app_logic_instance.load_file(path)
        clip_b = app_logic_instance.animation_file.clips[1]
        app_logic_instance.delete_items([clip_b])
        assert len(app_logic_instance.animation_file.clips) == 2
        
    def test_rename_clip_and_update_references(self, app_logic_instance):
        clip1 = AnimationClip("First", "S1", "L1", 1.0)
        # POPRAWKA: Przekazujemy argument bezpośrednio, a nie w zagnieżdżonym słowniku
        clip2 = AnimationClip("Second", "S1", "L1", 1.0, NextAnimationName="First")
        app_logic_instance.animation_file = AnimationFile()
        app_logic_instance.animation_file.clips = [clip1, clip2]
        app_logic_instance.rename_item(clip1, "First_Renamed")
        assert clip1.name == "First_Renamed"
        assert clip2.other_properties["NextAnimationName"] == "First_Renamed"
    
    def test_rename_segment_and_layer(self, app_logic_instance):
        clip = AnimationClip("A", "OldSeg", "OldLayer", 1.0, atom_id="Person")
        app_logic_instance.animation_file = AnimationFile()
        app_logic_instance.animation_file.clips = [clip]
        app_logic_instance.rename_item(("segment", "Person", "OldSeg"), "NewSeg")
        assert clip.segment == "NewSeg"
        app_logic_instance.rename_item(("layer", "Person", "NewSeg", "OldLayer"), "NewLayer")
        assert clip.layer == "NewLayer"

    def test_merge_layers(self, app_logic_instance):
        clip_a1 = AnimationClip("A1", "S1", "LayerA", 2.0, atom_id="Atom1")
        clip_a1.float_params.append(FloatParameter("Storable1", "ParamX", [], 0, 1))
        clip_b1 = AnimationClip("B1", "S1", "LayerB", 2.0, atom_id="Atom1")
        clip_b1.float_params.append(FloatParameter("Storable1", "ParamY", [], 0, 1))
        clip_a2_matching = AnimationClip("B1", "S1", "LayerA", 2.0, atom_id="Atom1")
        clip_a2_matching.float_params.append(FloatParameter("Storable1", "ParamZ", [], 0, 1))
        app_logic_instance.animation_file = AnimationFile()
        app_logic_instance.animation_file.clips = [clip_a1, clip_b1, clip_a2_matching]
        src_layer_data = ("layer", "Atom1", "S1", "LayerA")
        tgt_layer_data = ("layer", "Atom1", "S1", "LayerB")
        
        app_logic_instance.merge_layers(src_layer_data, tgt_layer_data)
        
        assert len(app_logic_instance.animation_file.clips) == 2
        param_names = {(p.storable, p.name) for p in app_logic_instance.animation_file.clips[1].float_params}
        assert {("Storable1", "ParamY"), ("Storable1", "ParamZ"), ("Storable1", "ParamX")} == param_names

    def test_merge_layers_with_conflicting_trigger_groups(self, app_logic_instance):
        clip_a = AnimationClip("CommonClip", "S1", "LayerA", 1.0, atom_id="A1")
        clip_a.trigger_groups.append(TriggerGroup("Audio 1", "1", []))
        clip_b = AnimationClip("CommonClip", "S1", "LayerB", 1.0, atom_id="A1")
        clip_b.trigger_groups.append(TriggerGroup("Audio 1", "1", []))
        app_logic_instance.animation_file = AnimationFile()
        app_logic_instance.animation_file.clips = [clip_a, clip_b]
        src_layer_data = ("layer", "A1", "S1", "LayerA")
        tgt_layer_data = ("layer", "A1", "S1", "LayerB")
        
        app_logic_instance.merge_layers(src_layer_data, tgt_layer_data)
        
        merged_clip = next(c for c in app_logic_instance.animation_file.clips if c.name == "CommonClip")
        assert len(merged_clip.trigger_groups) == 2
        tg_names = {tg.name for tg in merged_clip.trigger_groups}
        assert {"Audio 1", "Audio 1 (merged)"} == tg_names

    def test_move_clips_to_layer(self, app_logic_instance):
        c1 = AnimationClip("C1", "S1", "LayerA", 1.0, atom_id="A1")
        c3 = AnimationClip("C3", "S1", "LayerB", 1.0, atom_id="A1")
        app_logic_instance.animation_file = AnimationFile()
        app_logic_instance.animation_file.clips = [c1, c3]
        target_layer = ("layer", "A1", "S1", "LayerB")
        
        app_logic_instance.move_or_copy_clips_to_layer([id(c1)], target_layer, is_copy=False)
        
        assert c1.layer == "LayerB"
        assert c1.order_index == 1

    def test_move_clip_to_compatible_layer(self, app_logic_instance):
        clip_s1a = AnimationClip("S1A", "Seg1", "LayerA", 1.0, atom_id="A1")
        clip_s1a.controllers.append(ControllerTarget("hipControl"))
        clip_s2b = AnimationClip("S2B", "Seg2", "LayerB", 1.0, atom_id="A1")
        clip_s2b.controllers.append(ControllerTarget("hipControl"))
        app_logic_instance.animation_file = AnimationFile()
        app_logic_instance.animation_file.clips = [clip_s1a, clip_s2b]
        target_layer_data = ("layer", "A1", "Seg2", "LayerB")
        
        app_logic_instance.move_or_copy_clips_to_layer([id(clip_s1a)], target_layer_data, is_copy=False)
        
        assert clip_s1a.segment == "Seg2" and clip_s1a.layer == "LayerB"

    def test_move_clip_creates_new_layer(self, app_logic_instance):
        clip_s1a = AnimationClip("S1A", "Seg1", "LayerA", 1.0, atom_id="A1")
        clip_s1a.controllers.append(ControllerTarget("hipControl")) # Sygnatura A
        
        clip_s2x = AnimationClip("S2X", "Seg2", "LayerX", 1.0, atom_id="A1")
        clip_s2x.controllers.append(ControllerTarget("chestControl")) # Inna sygnatura B

        app_logic_instance.animation_file = AnimationFile()
        app_logic_instance.animation_file.clips = [clip_s1a, clip_s2x]
        target_layer_data = ("layer", "A1", "Seg2", "LayerX")
        
        app_logic_instance.move_or_copy_clips_to_layer([id(clip_s1a)], target_layer_data, is_copy=False)
        
        assert clip_s1a.segment == "Seg2" and clip_s1a.layer == "LayerA"

    def test_move_clip_creates_renamed_layer(self, app_logic_instance):
        clip_s1a = AnimationClip("S1A", "Seg1", "LayerA", 1.0, atom_id="A1")
        clip_s1a.controllers.append(ControllerTarget("hipControl"))
        clip_s2a = AnimationClip("S2A", "Seg2", "LayerA", 1.0, atom_id="A1")
        clip_s2a.controllers.append(ControllerTarget("chestControl"))
        
        app_logic_instance.animation_file = AnimationFile()
        app_logic_instance.animation_file.clips = [clip_s1a, clip_s2a]
        target_layer_data = ("layer", "A1", "Seg2", "LayerA")
        
        app_logic_instance.move_or_copy_clips_to_layer([id(clip_s1a)], target_layer_data, is_copy=False)
        
        assert clip_s1a.segment == "Seg2" and clip_s1a.layer == "LayerA_1"

    def test_center_root_on_first_frame(self, app_logic_instance):
        clip = AnimationClip("Walk", "S1", "L1", 1.0)
        root = ControllerTarget("hipControl")
        root.properties["X"] = [KeyframeEncoder.encode_keyframe(0.0, 1.5, 3, 0, 0)]
        root.properties["Z"] = [KeyframeEncoder.encode_keyframe(0.0, -3.0, 3, 0, 0)]
        clip.controllers.append(root)
        app_logic_instance.animation_file = AnimationFile()
        app_logic_instance.animation_file.clips = [clip]
        
        app_logic_instance.center_root_on_first_frame([clip])
        
        _, new_x, _ = KeyframeDecoder.decode_keyframe(root.properties["X"][0], 0, 0)
        _, new_z, _ = KeyframeDecoder.decode_keyframe(root.properties["Z"][0], 0, 0)
        assert abs(new_x) < 1e-6 and abs(new_z) < 1e-6

    def test_duplicate_clip(self, app_logic_instance):
        clip1 = AnimationClip("MyClip", "S1", "L1", 1.0)
        app_logic_instance.animation_file = AnimationFile()
        app_logic_instance.animation_file.clips = [clip1]
        
        app_logic_instance.duplicate_clip(clip1)
        
        assert len(app_logic_instance.animation_file.clips) == 2
        names = {c.name for c in app_logic_instance.animation_file.clips}
        assert {"MyClip", "MyClip (copy)"} == names

    def test_batch_rename_clips(self, app_logic_instance):
        clips = [AnimationClip("Anim_A", "S1", "L1", 1.0), AnimationClip("Anim_B", "S1", "L1", 1.0)]
        app_logic_instance.animation_file = AnimationFile()
        app_logic_instance.animation_file.clips = clips
        
        app_logic_instance.batch_rename_clips(clips, find="Anim_", replace="Motion_", prefix="", suffix="")
        
        names = {c.name for c in app_logic_instance.animation_file.clips}
        assert {"Motion_A", "Motion_B"} == names

    def test_internal_delete_targets_from_single_clip(self, app_logic_instance):
        """Tests the internal helper for deleting targets from a clip."""
        clip = AnimationClip("TestClip", "S1", "L1", 1.0)
        fp_to_delete = FloatParameter("s1", "p_delete", [], 0, 1)
        ct_to_delete = ControllerTarget("c_delete")
        clip.float_params = [fp_to_delete]
        clip.controllers = [ct_to_delete]
        
        deleted_count = app_logic_instance._delete_targets_from_single_clip(clip, [fp_to_delete, ct_to_delete])
        
        assert deleted_count == 2
        assert not clip.float_params
        assert not clip.controllers

    def test_process_target_deletion_scope_move(self, app_logic_instance):
        """Tests deleting a target and moving the clip to a new layer."""
        ct_common = ControllerTarget("hipControl")
        ct_to_delete = ControllerTarget("chestControl")
        
        clip_a = AnimationClip("A", "S1", "Base", 1.0)
        clip_a.controllers = [copy.deepcopy(ct_common), copy.deepcopy(ct_to_delete)]
        
        clip_b = AnimationClip("B", "S1", "Base", 1.0)
        clip_b.controllers = [copy.deepcopy(ct_common), copy.deepcopy(ct_to_delete)]

        app_logic_instance.animation_file = AnimationFile()
        app_logic_instance.animation_file.clips = [clip_a, clip_b]
        
        app_logic_instance.process_target_deletion(clip_a, [ct_to_delete], "move")
        
        assert len(app_logic_instance.animation_file.clips) == 2
        # Clip A should have changed
        assert len(clip_a.controllers) == 1
        assert clip_a.controllers[0].id == "hipControl"
        assert clip_a.layer == "Base_1" # It was moved to a new layer
        
        # Clip B should be unchanged
        assert len(clip_b.controllers) == 2
        assert clip_b.layer == "Base"

    def test_process_target_deletion_scope_layer(self, app_logic_instance):
        """Tests deleting a target from all clips in a layer."""
        ct_common = ControllerTarget("hipControl")
        ct_to_delete = ControllerTarget("chestControl")
        
        clip_a = AnimationClip("A", "S1", "Base", 1.0, atom_id="Person")
        clip_a.controllers = [copy.deepcopy(ct_common), copy.deepcopy(ct_to_delete)]
        
        clip_b = AnimationClip("B", "S1", "Base", 1.0, atom_id="Person")
        clip_b.controllers = [copy.deepcopy(ct_common), copy.deepcopy(ct_to_delete)]

        clip_other_seg = AnimationClip("C", "S2", "Base", 1.0, atom_id="Person")
        clip_other_seg.controllers = [copy.deepcopy(ct_common), copy.deepcopy(ct_to_delete)]

        app_logic_instance.animation_file = AnimationFile()
        app_logic_instance.animation_file.clips = [clip_a, clip_b, clip_other_seg]
        
        app_logic_instance.process_target_deletion(clip_a, [ct_to_delete], "layer")
        
        assert len(app_logic_instance.animation_file.clips) == 3
        # Both clips in the same layer should have changed
        assert len(clip_a.controllers) == 1
        assert clip_a.layer == "Base"
        assert len(clip_b.controllers) == 1
        assert clip_b.layer == "Base"
        
        # The clip in another segment should be untouched
        assert len(clip_other_seg.controllers) == 2
        assert clip_other_seg.layer == "Base"

class TestDropPrediction:
    @pytest.fixture
    def app_logic_with_clips(self, app_logic_instance):
        """Provides an AppLogic instance with a predefined set of clips for D&D tests."""
        ct_hip = ControllerTarget("hipControl")
        ct_chest = ControllerTarget("chestControl")
        
        # Layer "Base" has hip and chest controls
        clip_a = AnimationClip("A", "S1", "Base", 1.0)
        clip_a.controllers = [copy.deepcopy(ct_hip), copy.deepcopy(ct_chest)]
        
        # Layer "Simple" has only hip control
        clip_b = AnimationClip("B", "S1", "Simple", 1.0)
        clip_b.controllers = [copy.deepcopy(ct_hip)]

        # Another clip for reordering
        clip_c = AnimationClip("C", "S1", "Base", 1.0)
        clip_c.controllers = [copy.deepcopy(ct_hip), copy.deepcopy(ct_chest)]

        app_logic_instance.animation_file = AnimationFile()
        app_logic_instance.animation_file.clips = [clip_a, clip_b, clip_c]
        return app_logic_instance
    
    def test_predict_reorder(self, app_logic_with_clips):
        logic = app_logic_with_clips
        clip_a = logic.animation_file.clips[0]
        clip_c = logic.animation_file.clips[2]
        
        action, _ = logic.predict_drop_action([clip_a], clip_c, is_copy=False)
        assert action == DropActionType.REORDER_CLIPS
    
    def test_predict_move_to_compatible(self, app_logic_with_clips):
        logic = app_logic_with_clips
        # Create a new clip with the same signature as "Simple" layer
        clip_d = AnimationClip("D", "S2", "Other", 1.0)
        clip_d.controllers.append(ControllerTarget("hipControl"))
        logic.animation_file.clips.append(clip_d)
        
        target_clip = logic.animation_file.clips[1] # clip_b in "Simple" layer
        
        action, _ = logic.predict_drop_action([clip_d], target_clip, is_copy=False)
        assert action == DropActionType.MOVE_CLIPS_COMPATIBLE

    def test_predict_move_to_incompatible(self, app_logic_with_clips):
        logic = app_logic_with_clips
        clip_a = logic.animation_file.clips[0] # From "Base" layer (2 controllers)
        clip_b = logic.animation_file.clips[1] # From "Simple" layer (1 controller)
        
        # Try to move clip A to layer "Simple"
        action, _ = logic.predict_drop_action([clip_a], clip_b, is_copy=False)
        assert action == DropActionType.MOVE_CLIPS_NEW_LAYER

    def test_predict_copy_to_incompatible(self, app_logic_with_clips):
        logic = app_logic_with_clips
        clip_a = logic.animation_file.clips[0] # From "Base" layer
        clip_b = logic.animation_file.clips[1] # From "Simple" layer
        
        action, _ = logic.predict_drop_action([clip_a], clip_b, is_copy=True)
        assert action == DropActionType.COPY_CLIPS_NEW_LAYER

    def test_predict_merge_layers(self, app_logic_with_clips):
        logic = app_logic_with_clips
        # Data for layers "Base" and "Simple"
        layer_base_data = ('layer', '(Standalone)', 'S1', 'Base')
        layer_simple_data = ('layer', '(Standalone)', 'S1', 'Simple')

        action, _ = logic.predict_drop_action([layer_simple_data], layer_base_data, is_copy=False)
        assert action == DropActionType.MERGE_LAYERS

    def test_predict_invalid_merge_across_segments(self, app_logic_with_clips):
        logic = app_logic_with_clips
        layer_s1_data = ('layer', '(Standalone)', 'S1', 'Base')
        layer_s2_data = ('layer', '(Standalone)', 'S2', 'Other')

        action, _ = logic.predict_drop_action([layer_s1_data], layer_s2_data, is_copy=False)
        assert action == DropActionType.INVALID

    def test_predict_invalid_drop_clip_on_segment(self, app_logic_with_clips):
        logic = app_logic_with_clips
        clip_a = logic.animation_file.clips[0]
        segment_data = ('segment', '(Standalone)', 'S1')

        action, _ = logic.predict_drop_action([clip_a], segment_data, is_copy=False)
        assert action == DropActionType.INVALID

class TestFileMerging:
    @pytest.fixture
    def base_file_data(self):
        return {"SerializeVersion": "4", "AtomType": "Person", "Clips": [
            {"AnimationName": "BaseWalk", "AnimationSegment": "Locomotion", "AnimationLayer": "Base", "AnimationLength": "2.0"}
        ]}
    
    @pytest.fixture
    def merge_file_data(self):
        return {"SerializeVersion": "4", "AtomType": "Person", "Clips": [
            {"AnimationName": "MergedRun", "AnimationSegment": "Locomotion", "AnimationLayer": "Base", "AnimationLength": "1.5"},
            {"AnimationName": "MergedIdle", "AnimationSegment": "Idle", "AnimationLayer": "IdleLayer", "AnimationLength": "3.0"}
        ]}

    def test_successful_merge(self, app_logic_instance, temp_json_file, base_file_data, merge_file_data):
        base_path = temp_json_file("base.json", base_file_data)
        merge_path = temp_json_file("merge.json", merge_file_data)
        app_logic_instance.load_file(base_path)
        
        app_logic_instance.merge_animation_file(merge_path, conflict_strategy="rename")
        
        assert len(app_logic_instance.animation_file.clips) == 3
        names = {c.name for c in app_logic_instance.animation_file.clips}
        assert {"BaseWalk", "MergedRun", "MergedIdle"} == names

    def test_merge_with_name_conflict_rename(self, app_logic_instance, temp_json_file, base_file_data):
        merge_data_conflict = {"SerializeVersion": "4", "AtomType": "Person", "Clips": [
            {"AnimationName": "BaseWalk", "AnimationSegment": "Locomotion", "AnimationLayer": "Base", "AnimationLength": "2.0"}
        ]}
        base_path = temp_json_file("base.json", base_file_data)
        merge_path = temp_json_file("merge_conflict.json", merge_data_conflict)
        app_logic_instance.load_file(base_path)
        
        app_logic_instance.merge_animation_file(merge_path, conflict_strategy="rename")

        names = {c.name for c in app_logic_instance.animation_file.clips}
        assert {"BaseWalk", "BaseWalk_merged"} == names
    
    def test_merge_fails_on_mismatched_atom_type(self, app_logic_instance, temp_json_file, base_file_data):
        merge_data_mismatch = {"SerializeVersion": "4", "AtomType": "Cube", "Clips": []}
        base_path = temp_json_file("base.json", base_file_data)
        merge_path = temp_json_file("merge_mismatch.json", merge_data_mismatch)
        app_logic_instance.load_file(base_path)
        
        with pytest.raises(MergeError, match="Mismatched Atom Types"):
            app_logic_instance.merge_animation_file(merge_path, "rename")

    def test_merge_fails_into_scene(self, app_logic_instance, temp_json_file, sample_scene_file_data, merge_file_data):
        scene_path = temp_json_file("scene.json", sample_scene_file_data)
        merge_path = temp_json_file("merge.json", merge_file_data)
        app_logic_instance.load_file(scene_path)
        
        with pytest.raises(MergeError, match="Cannot merge into a scene file"):
            app_logic_instance.merge_animation_file(merge_path, "rename")

    def test_merge_fails_with_scene_source(self, app_logic_instance, temp_json_file, base_file_data, sample_scene_file_data):
        base_path = temp_json_file("base.json", base_file_data)
        scene_path = temp_json_file("scene.json", sample_scene_file_data)
        app_logic_instance.load_file(base_path)
        
        with pytest.raises(MergeError, match="Cannot merge a scene file"):
            app_logic_instance.merge_animation_file(scene_path, "rename")


@pytest.mark.qt
class TestUIInteractions:
    @pytest.fixture
    def main_window(self, qtbot):
        """Fixture do tworzenia instancji MainWindow dla testów UI."""
        window = MainWindow()
        qtbot.addWidget(window)
        return window

    def find_item_by_text(self, tree, text):
        """Pomocnicza funkcja do znajdowania elementu w drzewie po tekście."""
        for i in range(tree.topLevelItemCount()):
            top_item = tree.topLevelItem(i)
            found = self._find_item_recursive(top_item, text)
            if found:
                return found
        return None

    def _find_item_recursive(self, parent_item, text):
        if parent_item.text(0) == text:
            return parent_item
        for i in range(parent_item.childCount()):
            child_item = parent_item.child(i)
            found = self._find_item_recursive(child_item, text)
            if found:
                return found
        return None

    def test_tree_state_preservation_on_reorder(self, main_window, qtbot, temp_json_file):
        """
        Testuje, czy stan rozwinięcia/zwinięcia drzewa jest zachowywany po operacji
        zmieniającej kolejność klipów, która wywołuje odświeżenie widoku.
        """
        # 1. Przygotowanie danych i załadowanie pliku
        test_data = {
            "SerializeVersion": "4", "AtomType": "Person", "Clips": [
                {"AnimationName": "Walk", "AnimationSegment": "Locomotion", "AnimationLayer": "Base", "AnimationLength": "2.0"},
                {"AnimationName": "Walk_Slow", "AnimationSegment": "Locomotion", "AnimationLayer": "Base", "AnimationLength": "3.0"},
                {"AnimationName": "Jump", "AnimationSegment": "Locomotion", "AnimationLayer": "Overlay", "AnimationLength": "1.0"},
                {"AnimationName": "Wave", "AnimationSegment": "Gestures", "AnimationLayer": "Main", "AnimationLength": "1.5"},
            ]
        }
        path = temp_json_file("reorder_test.json", test_data)
        main_window.app_logic.load_file(path)
        
        # Początkowo drzewo jest całkowicie rozwinięte
        locomotion_segment_item = self.find_item_by_text(main_window.tree, "Segment: Locomotion")
        gestures_main_layer_item = self.find_item_by_text(main_window.tree, "  Layer: Main")
        
        assert locomotion_segment_item.isExpanded()
        assert gestures_main_layer_item.isExpanded()

        # 2. Ręczne zwinięcie niektórych węzłów
        locomotion_segment_item.setExpanded(False)
        gestures_main_layer_item.setExpanded(False)

        assert not locomotion_segment_item.isExpanded()
        assert not gestures_main_layer_item.isExpanded()

        # 3. Wykonanie operacji, która odświeża drzewo (zmiana kolejności klipów)
        clip_walk = next(c for c in main_window.app_logic.animation_file.clips if c.name == "Walk")
        clip_walk_slow = next(c for c in main_window.app_logic.animation_file.clips if c.name == "Walk_Slow")
        
        layer_data = ('layer', '(Standalone)', 'Locomotion', 'Base')
        dragged_ids = [id(clip_walk_slow)]
        target_id = id(clip_walk)
        
        # Symulacja przeciągnięcia "Walk_Slow" nad "Walk"
        main_window.app_logic.reorder_clips_in_layer(layer_data, dragged_ids, target_id, 'Above')

        # 4. Weryfikacja
        # Drzewo zostało odświeżone. Sprawdzamy, czy stan zwinięcia został zachowany.
        locomotion_segment_item_after = self.find_item_by_text(main_window.tree, "Segment: Locomotion")
        gestures_main_layer_item_after = self.find_item_by_text(main_window.tree, "  Layer: Main")
        
        assert locomotion_segment_item_after is not None
        assert gestures_main_layer_item_after is not None

        # To jest kluczowy test: węzły powinny pozostać zwinięte
        assert not locomotion_segment_item_after.isExpanded()
        assert not gestures_main_layer_item_after.isExpanded()

        # Dodatkowo, sprawdźmy czy węzeł, który nie był zwinięty, pozostał rozwinięty
        gestures_segment_item_after = self.find_item_by_text(main_window.tree, "Segment: Gestures")
        assert gestures_segment_item_after.isExpanded()