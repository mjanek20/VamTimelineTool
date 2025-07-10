# test_app_logic.py
import pytest
import os
import json
import copy

# Upewnij się, że ten plik jest w tym samym katalogu co inne moduły
from app_logic import AppLogic, MergeError
from data_models import AnimationFile, AnimationClip, ControllerTarget, FloatParameter, TriggerGroup
from keyframe_logic import KeyframeEncoder, KeyframeDecoder

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
        # POPRAWKA: Przywrócono dodawanie kontrolerów, aby sygnatury warstw były różne.
        clip_s1a = AnimationClip("S1A", "Seg1", "LayerA", 1.0, atom_id="A1")
        clip_s1a.controllers.append(ControllerTarget("hipControl")) # Sygnatura A
        
        clip_s2x = AnimationClip("S2X", "Seg2", "LayerX", 1.0, atom_id="A1")
        clip_s2x.controllers.append(ControllerTarget("chestControl")) # Inna sygnatura B

        app_logic_instance.animation_file = AnimationFile()
        app_logic_instance.animation_file.clips = [clip_s1a, clip_s2x]
        target_layer_data = ("layer", "A1", "Seg2", "LayerX")
        
        app_logic_instance.move_or_copy_clips_to_layer([id(clip_s1a)], target_layer_data, is_copy=False)
        
        # Klip powinien zostać przeniesiony do nowej warstwy 'LayerA' w 'Seg2'
        assert clip_s1a.segment == "Seg2" and clip_s1a.layer == "LayerA"

    def test_move_clip_creates_renamed_layer(self, app_logic_instance):
        clip_s1a = AnimationClip("S1A", "Seg1", "LayerA", 1.0, atom_id="A1")
        clip_s1a.controllers.append(ControllerTarget("hipControl"))
        clip_s2a = AnimationClip("S2A", "Seg2", "LayerA", 1.0, atom_id="A1")
        # Ważne: inna sygnatura, mimo tej samej nazwy warstwy
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
        
        # POPRAWKA: Dodano brakujące argumenty 'prefix' i 'suffix'
        app_logic_instance.batch_rename_clips(clips, find="Anim_", replace="Motion_", prefix="", suffix="")
        
        names = {c.name for c in app_logic_instance.animation_file.clips}
        assert {"Motion_A", "Motion_B"} == names

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