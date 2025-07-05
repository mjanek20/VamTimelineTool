import sys
import json
import copy
import struct
from collections import defaultdict

# Using PyQt6 for the GUI
from PyQt6.QtCore import Qt, QMimeData, QDateTime
from PyQt6.QtGui import QAction, QIcon, QDrag
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTreeWidget, QTreeWidgetItem, QAbstractItemView, QFileDialog, QLabel,
    QMenu, QMessageBox, QInputDialog, QToolBar, QFormLayout, QLineEdit,
    QListWidget, QPlainTextEdit, QPushButton, QDialog, QDialogButtonBox
)

# --- 1. Keyframe Encoding Logic ---

class KeyframeEncoder:
    """
    Replicates the keyframe encoding/decoding logic from AtomAnimationSerializer.cs.
    This is crucial for creating new "empty" animation data that the plugin can read.
    """
    @staticmethod
    def encode_keyframe(time: float, value: float, curve_type: int, last_v: float, last_c: int) -> str:
        """Encodes a single keyframe into the plugin's string format."""
        sb = []
        
        has_value = abs(last_v - value) > 1e-7
        has_curve_type = last_c != curve_type

        # Encode flags into the first character
        encoded_value = 0
        if has_value: encoded_value |= (1 << 0)
        if has_curve_type: encoded_value |= (1 << 1)
        sb.append(chr(ord('A') + encoded_value))

        # Pack time, value, and curve_type into hex strings
        sb.append(struct.pack('<f', time).hex().upper())
        if has_value:
            sb.append(struct.pack('<f', value).hex().upper())
        if has_curve_type:
            sb.append(struct.pack('<B', curve_type).hex().upper())
            
        return "".join(sb)

# --- 2. Data Model ---

class FloatParameter:
    def __init__(self, storable, name, value, min_val, max_val):
        self.storable = storable
        self.name = name
        self.value = value
        self.min = min_val
        self.max = max_val

    @classmethod
    def from_dict(cls, data):
        return cls(data.get("Storable"), data.get("Name"), data.get("Value", []), data.get("Min"), data.get("Max"))

    def to_dict(self):
        props = {"Storable": self.storable, "Name": self.name, "Value": self.value}
        if self.min is not None: props["Min"] = self.min
        if self.max is not None: props["Max"] = self.max
        return props

class ControllerTarget:
    def __init__(self, controller_id, **kwargs):
        self.id = controller_id
        self.properties = kwargs
        for key in ['X', 'Y', 'Z', 'RotX', 'RotY', 'RotZ', 'RotW']:
            if key not in self.properties:
                self.properties[key] = []

    @classmethod
    def from_dict(cls, data):
        controller_id = data.get("Controller")
        properties = {k: v for k, v in data.items() if k != "Controller"}
        return cls(controller_id, **properties)

    def to_dict(self):
        data = {"Controller": self.id}
        data.update(self.properties)
        return data

class AnimationClip:
    def __init__(self, name, segment, layer, length, order_index=0, **kwargs):
        self.name = name
        self.segment = segment
        self.layer = layer
        self.length = length
        self.order_index = order_index
        self.other_properties = kwargs
        self.float_params = []
        self.controllers = []

    @classmethod
    def from_dict(cls, data):
        known_keys = {"AnimationName", "AnimationSegment", "AnimationLayer", "AnimationLength", "FloatParams", "Controllers", "OrderIndex"}
        instance = cls(
            name=data.get("AnimationName", "Unnamed"),
            segment=data.get("AnimationSegment", "Default"),
            layer=data.get("AnimationLayer", "Default"),
            length=float(data.get("AnimationLength", 0.0)),
            order_index=int(data.get("OrderIndex", 0)),
            **{k: v for k, v in data.items() if k not in known_keys}
        )
        if "FloatParams" in data:
            instance.float_params = [FloatParameter.from_dict(p) for p in data["FloatParams"]]
        if "Controllers" in data:
            instance.controllers = [ControllerTarget.from_dict(c) for c in data["Controllers"]]
        return instance

    def to_dict(self):
        data = {
            "AnimationName": self.name,
            "AnimationSegment": self.segment,
            "AnimationLayer": self.layer,
            "AnimationLength": str(self.length),
        }
        data.update(self.other_properties)
        if self.float_params:
            data["FloatParams"] = [p.to_dict() for p in sorted(self.float_params, key=lambda p: (p.storable, p.name))]
        if self.controllers:
            data["Controllers"] = [c.to_dict() for c in sorted(self.controllers, key=lambda c: c.id)]
        return data

class AnimationFile:
    def __init__(self, version, atom_type):
        self.version = version
        self.atom_type = atom_type
        self.clips = []

    @classmethod
    def from_dict(cls, data):
        instance = cls(data.get("SerializeVersion"), data.get("AtomType"))
        if "Clips" in data:
            # Assign an order index to preserve original file order
            for i, clip_data in enumerate(data["Clips"]):
                clip_data['OrderIndex'] = i
            instance.clips = [AnimationClip.from_dict(c) for c in data["Clips"]]
        return instance

    def to_dict(self):
        # Sort clips by their order index before saving to maintain user's arrangement
        self.clips.sort(key=lambda c: c.order_index)
        return {
            "SerializeVersion": self.version,
            "AtomType": self.atom_type,
            "Clips": [c.to_dict() for c in self.clips]
        }

# --- 3. Custom UI Components ---

class AnimationTreeWidget(QTreeWidget):
    def __init__(self, parent_window):
        super().__init__()
        self.parent_window = parent_window
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.open_context_menu)
        
        # --- Style for better selection visibility ---
        self.setStyleSheet("""
            QTreeWidget::item:selected {
                background-color: #a8d8ff;
                color: black;
            }
        """)

        self.itemDoubleClicked.connect(self.on_item_double_clicked)
    
    def on_item_double_clicked(self, item, column):
        self.parent_window.rename_selected_item()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_F2:
            self.parent_window.rename_selected_item()
        elif event.key() == Qt.Key.Key_Delete:
            self.parent_window.delete_selected_items()
        else:
            super().keyPressEvent(event)

    def dragEnterEvent(self, event):
        if event.source() == self and event.mimeData().text() in ["clip-drag", "layer-drag"]:
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.source() == self and event.mimeData().text() in ["clip-drag", "layer-drag"]:
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)
            
    def startDrag(self, supportedActions):
        items = self.selectedItems()
        if not items: return
        
        item = items[0]
        drag = QDrag(self)
        mime_data = QMimeData()
        
        # Dragging a layer
        if item.parent() and not item.parent().parent():
            if len(items) > 1: return # Only allow single layer drag
            mime_data.setText("layer-drag")
            drag.setMimeData(mime_data)
            drag.exec(Qt.DropAction.MoveAction)
        # Dragging a clip
        elif item.parent() and item.parent().parent():
            mime_data.setText("clip-drag")
            drag.setMimeData(mime_data)
            # Allow both Move and Copy actions
            drag.exec(Qt.DropAction.MoveAction | Qt.DropAction.CopyAction, Qt.DropAction.MoveAction)
    
    def dropEvent(self, event):
        mime_text = event.mimeData().text()
        if mime_text == "clip-drag":
            self.handle_clip_drop(event)
        elif mime_text == "layer-drag":
            self.handle_layer_merge(event)
        else:
            event.ignore()
            
    def get_layer_clips(self, segment_name, layer_name):
        return [c for c in self.parent_window.animation_file.clips if c.segment == segment_name and c.layer == layer_name]

    def get_layer_target_signature(self, segment_name, layer_name):
        clips = self.get_layer_clips(segment_name, layer_name)
        if not clips: return frozenset(), frozenset()
        
        float_params_keys = {(p.storable, p.name) for c in clips for p in c.float_params}
        controller_ids = {c.id for clip in clips for c in clip.controllers}
        return frozenset(float_params_keys), frozenset(controller_ids)

    def handle_layer_merge(self, event):
        source_layer_item = self.selectedItems()[0]
        target_item_at_point = self.itemAt(event.position().toPoint())
        
        target_layer_item = None
        if target_item_at_point:
            if target_item_at_point.parent() and not target_item_at_point.parent().parent(): # Dropped on layer
                target_layer_item = target_item_at_point
            elif target_item_at_point.parent() and target_item_at_point.parent().parent(): # Dropped on clip
                target_layer_item = target_item_at_point.parent()
        
        if not target_layer_item or source_layer_item == target_layer_item:
            event.ignore(); return
            
        if source_layer_item.parent() != target_layer_item.parent():
            QMessageBox.warning(self, "Invalid Operation", "Layers can only be merged within the same segment.")
            return

        source_layer_name_clean = source_layer_item.text(0).strip()
        target_layer_name_clean = target_layer_item.text(0).strip()
        reply = QMessageBox.question(self, 'Confirm Layer Merge',
                                     f"Are you sure you want to merge '{source_layer_name_clean}' into '{target_layer_name_clean}'?\n\n"
                                     "This will add missing targets to all animations in the target layer and cannot be undone.",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        
        if reply == QMessageBox.StandardButton.No:
            event.ignore(); return

        segment_name = target_layer_item.parent().text(0).replace("Segment: ", "").strip()
        source_layer_name = source_layer_item.text(0).replace("  Layer: ", "").strip()
        target_layer_name = target_layer_item.text(0).replace("  Layer: ", "").strip()
        self.parent_window.log_message(f"Initiating merge of layer '{source_layer_name}' into '{target_layer_name}' in segment '{segment_name}'.")

        source_clips = self.get_layer_clips(segment_name, source_layer_name)
        target_clips = self.get_layer_clips(segment_name, target_layer_name)

        # Create a master list of all targets from both layers
        master_float_params = { (p.storable, p.name): p for clip in source_clips + target_clips for p in clip.float_params }
        master_controllers = { c.id: c for clip in source_clips + target_clips for c in clip.controllers }

        # Merge clips with the same name, move the others
        target_clips_by_name = {clip.name: clip for clip in target_clips}
        for source_clip in source_clips:
            if source_clip.name in target_clips_by_name:
                self.parent_window.log_message(f"Merging clip '{source_clip.name}' from source layer into target layer.")
                target_clip = target_clips_by_name[source_clip.name]
                # Merge FloatParams
                existing_fp_keys = {(p.storable, p.name) for p in target_clip.float_params}
                for param in source_clip.float_params:
                    if (param.storable, param.name) not in existing_fp_keys:
                        target_clip.float_params.append(param)
                # Merge Controllers
                existing_controller_ids = {c.id for c in target_clip.controllers}
                for controller in source_clip.controllers:
                    if controller.id not in existing_controller_ids:
                        target_clip.controllers.append(controller)
                self.parent_window.animation_file.clips.remove(source_clip)
            else:
                # Just move the clip to the new layer
                self.parent_window.log_message(f"Moving clip '{source_clip.name}' to target layer '{target_layer_name}'.")
                source_clip.layer = target_layer_name
        
        # Ensure all clips in the target layer have all master targets
        self.parent_window.log_message(f"Harmonizing targets for all clips in layer '{target_layer_name}'...")
        final_target_clips = self.get_layer_clips(segment_name, target_layer_name)
        for clip in final_target_clips:
            # Add missing float params
            clip_fp_keys = {(p.storable, p.name) for p in clip.float_params}
            for target_key, template_param in master_float_params.items():
                if target_key not in clip_fp_keys:
                    kf1 = KeyframeEncoder.encode_keyframe(0.0, 0.0, 3, 0.0, -1)
                    kf2 = KeyframeEncoder.encode_keyframe(clip.length, 0.0, 3, 0.0, 3)
                    clip.float_params.append(FloatParameter(
                        template_param.storable, template_param.name, [kf1, kf2], 
                        template_param.min, template_param.max))
            
            # Add missing controllers
            clip_controller_ids = {c.id for c in clip.controllers}
            for controller_id, template_controller in master_controllers.items():
                if controller_id not in clip_controller_ids:
                    new_controller = ControllerTarget(controller_id, **copy.deepcopy(template_controller.properties))
                    pos_val, rot_val, rotw_val = 0.0, 0.0, 1.0
                    for axis in ['X', 'Y', 'Z']:
                        kf1 = KeyframeEncoder.encode_keyframe(0.0, pos_val, 3, 0.0, -1)
                        kf2 = KeyframeEncoder.encode_keyframe(clip.length, pos_val, 3, pos_val, 3)
                        new_controller.properties[axis] = [kf1, kf2]
                    for axis in ['RotX', 'RotY', 'RotZ']:
                        kf1 = KeyframeEncoder.encode_keyframe(0.0, rot_val, 3, 0.0, -1)
                        kf2 = KeyframeEncoder.encode_keyframe(clip.length, rot_val, 3, rot_val, 3)
                        new_controller.properties[axis] = [kf1, kf2]
                    kf1_w = KeyframeEncoder.encode_keyframe(0.0, rotw_val, 3, 0.0, -1)
                    kf2_w = KeyframeEncoder.encode_keyframe(clip.length, rotw_val, 3, rotw_val, 3)
                    new_controller.properties['RotW'] = [kf1_w, kf2_w]
                    clip.controllers.append(new_controller)

        self.parent_window.log_message(f"Layer merge complete. '{source_layer_name}' has been merged into '{target_layer_name}'.")
        self.parent_window.populate_animation_tree()
        event.acceptProposedAction()

    def handle_clip_drop(self, event):
        source_items = self.selectedItems()
        target_item = self.itemAt(event.position().toPoint())
        if not source_items or not target_item:
            event.ignore(); return

        is_copy = event.proposedAction() == Qt.DropAction.CopyAction
        source_layer_item = source_items[0].parent()

        # Determine the target layer
        target_layer_item = None
        if isinstance(target_item.data(0, 1000), AnimationClip):
            target_layer_item = target_item.parent()
        elif target_item.childCount() > 0 and isinstance(target_item.child(0).data(0, 1000), AnimationClip):
            target_layer_item = target_item
        
        if not target_layer_item:
            event.ignore(); return
        
        # Case 1: Reordering within the same layer
        if not is_copy and source_layer_item == target_layer_item:
            self.reorder_clips_in_layer(source_items, target_item, event)
        # Case 2: Moving or copying to a different layer/segment
        else:
            self.move_or_copy_clips_to_layer(source_items, target_layer_item, is_copy, event)
            
        self.parent_window.populate_animation_tree()
        event.acceptProposedAction()

    def reorder_clips_in_layer(self, source_items, target_item, event):
        layer_item = source_items[0].parent()
        segment_name = layer_item.parent().text(0).replace("Segment: ", "").strip()
        layer_name = layer_item.text(0).replace("  Layer: ", "").strip()
        
        clips_in_layer = self.get_layer_clips(segment_name, layer_name)
        clips_in_layer.sort(key=lambda c: c.order_index)

        dragged_clip_objs = [item.data(0, 1000) for item in source_items]
        remaining_clips = [clip for clip in clips_in_layer if clip not in dragged_clip_objs]
        
        drop_pos_indicator = self.dropIndicatorPosition()
        target_clip_obj = target_item.data(0, 1000) if isinstance(target_item.data(0, 1000), AnimationClip) else None

        if target_clip_obj and target_clip_obj in remaining_clips:
            target_index = remaining_clips.index(target_clip_obj)
            if drop_pos_indicator == QAbstractItemView.DropIndicatorPosition.BelowItem:
                target_index += 1
        else: # Dropped on the layer item itself or empty space
            target_index = len(remaining_clips)
            
        # Insert dragged clips at the target index
        for clip_obj in reversed(dragged_clip_objs):
            remaining_clips.insert(target_index, clip_obj)

        # Re-assign order_index for all clips in the layer
        for i, clip_obj in enumerate(remaining_clips):
            clip_obj.order_index = i
        
        clip_names_str = ", ".join(f"'{c.name}'" for c in dragged_clip_objs)
        self.parent_window.log_message(f"Reordered {len(dragged_clip_objs)} clip(s) within layer '{layer_name}' (Segment: '{segment_name}'): {clip_names_str}.")

    def move_or_copy_clips_to_layer(self, source_items, target_layer_item, is_copy, event):
        source_layer_name = source_items[0].parent().text(0).replace("  Layer: ", "").strip()
        source_segment_name = source_items[0].parent().parent().text(0).replace("Segment: ", "").strip()
        
        target_layer_name = target_layer_item.text(0).replace("  Layer: ", "").strip()
        target_segment_item = target_layer_item.parent()
        target_segment_name = target_segment_item.text(0).replace("Segment: ", "").strip()

        source_fp_sig, source_c_sig = self.get_layer_target_signature(source_segment_name, source_layer_name)
        
        # If moving to a different segment, check for a compatible layer or create a new one
        if source_segment_name != target_segment_name:
            compatible_layer_name = None
            for i in range(target_segment_item.childCount()):
                layer_item = target_segment_item.child(i)
                layer_name = layer_item.text(0).replace("  Layer: ", "").strip()
                fp_sig, c_sig = self.get_layer_target_signature(target_segment_name, layer_name)
                if fp_sig == source_fp_sig and c_sig == source_c_sig:
                    compatible_layer_name = layer_name
                    break
            
            if compatible_layer_name:
                target_layer_name = compatible_layer_name
            else:
                # No compatible layer found, create a new one
                new_layer_name = source_layer_name
                existing_target_layer_names = {target_segment_item.child(i).text(0).replace("  Layer: ", "").strip() for i in range(target_segment_item.childCount())}
                counter = 1
                while new_layer_name in existing_target_layer_names:
                    new_layer_name = f"{source_layer_name}_{counter}"
                    counter += 1
                target_layer_name = new_layer_name
                self.parent_window.log_message(f"Created new layer '{target_layer_name}' in segment '{target_segment_name}' due to incompatible targets.")

        clips_in_target_layer = self.get_layer_clips(target_segment_name, target_layer_name)
        existing_names_in_target = {c.name for c in clips_in_target_layer}
        max_order_index = max((c.order_index for c in clips_in_target_layer), default=-1)

        for source_item in source_items:
            source_clip_obj = source_item.data(0, 1000)
            new_name = source_clip_obj.name
            
            if new_name in existing_names_in_target:
                reply = QMessageBox.question(self, "Name Conflict", f"A clip named '{new_name}' already exists in the target layer. Replace it?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if reply == QMessageBox.StandardButton.Yes:
                    clip_to_remove = next((c for c in clips_in_target_layer if c.name == new_name), None)
                    if clip_to_remove: self.parent_window.animation_file.clips.remove(clip_to_remove)
                    self.parent_window.log_message(f"Replaced clip '{new_name}' in '{target_segment_name}/{target_layer_name}'.")
                else: 
                    self.parent_window.log_message(f"Skipped {'copy' if is_copy else 'move'} of clip '{new_name}' due to name conflict.")
                    continue

            max_order_index += 1
            if is_copy:
                new_clip_obj = copy.deepcopy(source_clip_obj)
                new_clip_obj.name = new_name
                new_clip_obj.segment = target_segment_name
                new_clip_obj.layer = target_layer_name
                new_clip_obj.order_index = max_order_index
                self.parent_window.animation_file.clips.append(new_clip_obj)
                existing_names_in_target.add(new_name)
                self.parent_window.log_message(f"Copied clip '{source_clip_obj.name}' to '{target_segment_name}/{target_layer_name}'.")

            else:
                self.parent_window.log_message(f"Moved clip '{source_clip_obj.name}' from '{source_segment_name}/{source_layer_name}' to '{target_segment_name}/{target_layer_name}'.")
                source_clip_obj.name = new_name
                source_clip_obj.segment = target_segment_name
                source_clip_obj.layer = target_layer_name
                source_clip_obj.order_index = max_order_index
        
    def open_context_menu(self, position):
        menu = QMenu(self)
        selected_items = self.selectedItems()

        if selected_items:
            item = selected_items[0]
            # --- Single Item Actions ---
            if len(selected_items) == 1:
                rename_action = menu.addAction("Rename...")
                rename_action.setShortcut("F2")
                rename_action.triggered.connect(self.parent_window.rename_selected_item)

                if item.parent() and item.parent().parent(): # Is a clip item
                    duplicate_action = menu.addAction("Duplicate Clip")
                    duplicate_action.setShortcut("Ctrl+D")
                    duplicate_action.triggered.connect(self.parent_window.duplicate_selected_clip)

            # --- Multi-Item Actions ---
            # Action to delete any type of selected item (clip, layer, segment)
            delete_action = menu.addAction(QIcon.fromTheme("edit-delete"), f"Delete {len(selected_items)} selected item(s)")
            delete_action.setShortcut("Delete")
            delete_action.triggered.connect(self.parent_window.delete_selected_items)
        
        if not menu.isEmpty():
            menu.exec(self.viewport().mapToGlobal(position))

class ClipPropertiesPanel(QWidget):
    """A widget to display and edit properties of a selected AnimationClip."""
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.clip = None
        self.current_tree_item = None
        self.init_ui()
        self.clear() # Initially hidden

    def init_ui(self):
        self.layout = QVBoxLayout(self)
        self.form_layout = QFormLayout()

        # Editable Name
        self.name_edit = QLineEdit()
        self.name_edit.editingFinished.connect(self.on_name_changed)
        self.form_layout.addRow("Name:", self.name_edit)

        self.layout.addLayout(self.form_layout)
        
        # General Info
        self.layout.addWidget(QLabel("<b>General</b>"))
        self.general_form_layout = QFormLayout()
        self.segment_label = QLabel()
        self.layer_label = QLabel()
        self.length_label = QLabel()
        self.loop_label = QLabel()
        self.general_form_layout.addRow("Segment:", self.segment_label)
        self.general_form_layout.addRow("Layer:", self.layer_label)
        self.general_form_layout.addRow("Length:", self.length_label)
        self.general_form_layout.addRow("Loop:", self.loop_label)
        self.layout.addLayout(self.general_form_layout)

        # Sequencing Info
        self.layout.addWidget(QLabel("<b>Sequencing</b>"))
        self.sequence_form_layout = QFormLayout()
        self.next_anim_label = QLabel()
        self.sequence_form_layout.addRow("Next Animation:", self.next_anim_label)
        self.layout.addLayout(self.sequence_form_layout)

        # Targets List
        self.layout.addWidget(QLabel("<b>Targets</b>"))
        self.targets_list = QListWidget()
        self.layout.addWidget(self.targets_list)

        self.layout.addStretch()

    def display_clip_properties(self, clip, item):
        self.clip = clip
        self.current_tree_item = item
        
        # Temporarily block signals to prevent on_name_changed from firing
        self.name_edit.blockSignals(True)
        self.name_edit.setText(clip.name)
        self.name_edit.blockSignals(False)
        
        self.segment_label.setText(clip.segment)
        self.layer_label.setText(clip.layer)
        self.length_label.setText(f"{clip.length:.3f}s")
        
        is_loop = clip.other_properties.get('Loop', '0') == '1'
        self.loop_label.setText("Yes" if is_loop else "No")
        
        next_anim = clip.other_properties.get('NextAnimationName', 'None')
        self.next_anim_label.setText(next_anim)

        self.targets_list.clear()
        
        targets = []
        for controller in clip.controllers:
            targets.append(f"[C] {controller.id}")
        for param in clip.float_params:
            targets.append(f"[F] {param.storable}/{param.name}")
        
        if targets:
            self.targets_list.addItems(sorted(targets))
        else:
            self.targets_list.addItem("No targets in this clip.")

        self.show()

    def on_name_changed(self):
        if not self.clip: return
        self.main_window.update_clip_name(
            self.clip, self.current_tree_item, self.name_edit.text()
        )

    def clear(self):
        self.clip = None
        self.current_tree_item = None
        self.hide()


# --- 4. Main Application Window ---
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.animation_file = None
        self.setWindowTitle("VamTimeline Animation Editor")
        self.setGeometry(100, 100, 1200, 800)
        self.init_ui()

    def init_ui(self):
        # --- Actions ---
        open_action = QAction(QIcon.fromTheme("document-open"), "&Open...", self)
        open_action.triggered.connect(self.open_file)

        save_as_action = QAction(QIcon.fromTheme("document-save-as"), "&Save As...", self)
        save_as_action.triggered.connect(self.save_file_as)

        exit_action = QAction("E&xit", self)
        exit_action.triggered.connect(self.close)

        new_segment_action = QAction(QIcon.fromTheme("list-add"), "New &Segment...", self)
        new_segment_action.triggered.connect(self.create_new_segment)
        
        rename_action = QAction(QIcon.fromTheme("edit-rename"), "Re&name...", self)
        rename_action.setShortcut("F2")
        rename_action.triggered.connect(self.rename_selected_item)

        batch_rename_action = QAction("Change Names in &Batch...", self)
        batch_rename_action.triggered.connect(self.batch_rename_items)

        delete_action = QAction(QIcon.fromTheme("edit-delete"), "&Delete Selected", self)
        delete_action.setShortcut("Delete")
        delete_action.triggered.connect(self.delete_selected_items)

        duplicate_action = QAction(QIcon.fromTheme("edit-copy"), "&Duplicate Clip", self)
        duplicate_action.setShortcut("Ctrl+D")
        duplicate_action.triggered.connect(self.duplicate_selected_clip)

        # --- Menu Bar ---
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("&File")
        file_menu.addAction(open_action)
        file_menu.addAction(save_as_action)
        file_menu.addSeparator()
        file_menu.addAction(new_segment_action)
        file_menu.addSeparator()
        file_menu.addAction(exit_action)
        
        edit_menu = menu_bar.addMenu("&Edit")
        edit_menu.addAction(rename_action)
        edit_menu.addAction(batch_rename_action)
        edit_menu.addAction(duplicate_action)
        edit_menu.addSeparator()
        edit_menu.addAction(delete_action)
        
        # --- Toolbar ---
        toolbar = self.addToolBar("Main Toolbar")
        toolbar.addAction(open_action)
        toolbar.addAction(save_as_action)
        toolbar.addSeparator()
        toolbar.addAction(new_segment_action)
        toolbar.addAction(delete_action)
        
        # --- Main Layout ---
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)
        
        # Left Panel (Tree)
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_panel.setFixedWidth(400)

        # --- Filter and Control Buttons Layout ---
        filter_layout = QHBoxLayout()
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter animations...")
        self.filter_edit.textChanged.connect(self.filter_tree)
        filter_layout.addWidget(self.filter_edit)
        
        self.fold_all_button = QPushButton("Fold All")
        self.fold_all_button.clicked.connect(self.fold_all_items)
        filter_layout.addWidget(self.fold_all_button)
        
        self.unfold_all_button = QPushButton("Unfold All")
        self.unfold_all_button.clicked.connect(self.unfold_all_items)
        filter_layout.addWidget(self.unfold_all_button)
        
        left_layout.addLayout(filter_layout)
        
        self.tree = AnimationTreeWidget(self)
        self.tree.setHeaderLabels(["Segment / Layer / Animation"])
        self.tree.itemSelectionChanged.connect(self.on_tree_selection_changed)
        self.tree.itemChanged.connect(self.on_item_renamed)
        left_layout.addWidget(self.tree)
        
        # Right Panel (Properties and Log)
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        
        self.placeholder_label = QLabel("Select a clip to see its properties.")
        self.placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.properties_panel = ClipPropertiesPanel(self)
        
        self.log_console = QPlainTextEdit()
        self.log_console.setReadOnly(True)
        self.log_console.setFixedHeight(150)
        self.log_console.setStyleSheet("background-color: #f0f0f0; font-family: Consolas, monospace;")
        
        right_layout.addWidget(self.placeholder_label)
        right_layout.addWidget(self.properties_panel)
        right_layout.addStretch(1) # Add stretch factor to push log to bottom
        right_layout.addWidget(QLabel("<b>Console Log</b>"))
        right_layout.addWidget(self.log_console)
        
        main_layout.addWidget(left_panel)
        main_layout.addWidget(right_panel)
        
        self.log_message("Application started.")

    def get_tree_collapse_state(self):
        """
        Saves the state of COLLAPSED items. This implements an "open by default" policy.
        """
        state = set()
        for i in range(self.tree.topLevelItemCount()):
            segment_item = self.tree.topLevelItem(i)
            segment_key = segment_item.text(0)
            if not segment_item.isExpanded():
                state.add(segment_key)
            else:
                for j in range(segment_item.childCount()):
                    layer_item = segment_item.child(j)
                    if not layer_item.isExpanded():
                        layer_key = f"{segment_key}::{layer_item.text(0)}"
                        state.add(layer_key)
        return state

    def fold_all_items(self):
        """Collapses all items in the tree view."""
        if self.tree:
            self.tree.collapseAll()
            self.log_message("All items folded.")

    def unfold_all_items(self):
        """Expands all items in the tree view."""
        if self.tree:
            self.tree.expandAll()
            self.log_message("All items unfolded.")

    def log_message(self, message):
        """Appends a timestamped message to the console log."""
        timestamp = QDateTime.currentDateTime().toString("hh:mm:ss")
        self.log_console.appendPlainText(f"[{timestamp}] {message}")

    def on_tree_selection_changed(self):
        selected_items = self.tree.selectedItems()
        if selected_items:
            item = selected_items[0]
            clip_data = item.data(0, 1000)
            if isinstance(clip_data, AnimationClip):
                self.properties_panel.display_clip_properties(clip_data, item)
                self.placeholder_label.hide()
            else:
                self.properties_panel.clear()
                self.placeholder_label.show()
        else:
            self.properties_panel.clear()
            self.placeholder_label.show()

    def filter_tree(self, text):
        """Filters the tree based on the input text."""
        search_text = text.lower()
        is_filtering = bool(search_text)
        
        for i in range(self.tree.topLevelItemCount()):
            segment_item = self.tree.topLevelItem(i)
            segment_visible = False
            for j in range(segment_item.childCount()):
                layer_item = segment_item.child(j)
                layer_visible = False
                for k in range(layer_item.childCount()):
                    clip_item = layer_item.child(k)
                    if search_text in clip_item.text(0).lower():
                        clip_item.setHidden(False)
                        layer_visible = True
                    else:
                        clip_item.setHidden(True)
                
                if layer_visible or search_text in layer_item.text(0).lower():
                    layer_item.setHidden(False)
                    segment_visible = True
                    if is_filtering:
                        layer_item.setExpanded(True)
                else:
                    layer_item.setHidden(True)

            if segment_visible or search_text in segment_item.text(0).lower():
                segment_item.setHidden(False)
                if is_filtering:
                    segment_item.setExpanded(True)
            else:
                segment_item.setHidden(True)
        
        if not is_filtering:
            # If filter is cleared, repopulate to restore the original collapse state
            self.populate_animation_tree()

    def open_file(self):
        file_name, _ = QFileDialog.getOpenFileName(self, "Open Animation File", "", "JSON Files (*.json)")
        if file_name:
            try:
                with open(file_name, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.animation_file = AnimationFile.from_dict(data)
                self.populate_animation_tree(is_first_load=True)
                self.setWindowTitle(f"VamTimeline Animation Editor - {file_name}")
                self.log_message(f"File opened: {file_name}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Error loading file: {e}")
                self.log_message(f"ERROR: Failed to load file. Reason: {e}")

    def save_file_as(self):
        if not self.animation_file: 
            self.log_message("Save cancelled: No animation data loaded.")
            return
        file_name, _ = QFileDialog.getSaveFileName(self, "Save Animation File As", "", "JSON Files (*.json)")
        if file_name:
            if not file_name.lower().endswith('.json'):
                file_name += '.json'
            try:
                with open(file_name, 'w', encoding='utf-8') as f:
                    json.dump(self.animation_file.to_dict(), f, indent=3, ensure_ascii=False)
                self.setWindowTitle(f"VamTimeline Animation Editor - {file_name}")
                self.log_message(f"File saved as: {file_name}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Error saving file: {e}")
                self.log_message(f"ERROR: Failed to save file. Reason: {e}")

    def populate_animation_tree(self, is_first_load=False):
        self.tree.itemSelectionChanged.disconnect(self.on_tree_selection_changed)
        
        collapse_state = {} if is_first_load else self.get_tree_collapse_state()
                        
        self.tree.clear()
        if not self.animation_file: 
            self.tree.itemSelectionChanged.connect(self.on_tree_selection_changed)
            return

        grouped_clips = defaultdict(lambda: defaultdict(list))
        for clip in self.animation_file.clips:
            grouped_clips[clip.segment][clip.layer].append(clip)

        for segment_name in sorted(grouped_clips.keys()):
            layers = grouped_clips[segment_name]
            segment_item = QTreeWidgetItem(self.tree, [f"Segment: {segment_name}"])
            segment_item.setData(0, 1000, "segment")
            segment_item.setFlags(segment_item.flags() | Qt.ItemFlag.ItemIsEditable)
            segment_key = segment_item.text(0)
            segment_item.setExpanded(segment_key not in collapse_state)
            
            for layer_name in sorted(layers.keys()):
                clips = layers[layer_name]
                layer_item = QTreeWidgetItem(segment_item, [f"  Layer: {layer_name}"])
                layer_item.setData(0, 1000, "layer")
                layer_item.setFlags(layer_item.flags() | Qt.ItemFlag.ItemIsEditable)
                layer_key = f"{segment_key}::{layer_item.text(0)}"
                layer_item.setExpanded(layer_key not in collapse_state)
                
                clips.sort(key=lambda c: c.order_index)
                for clip_obj in clips:
                    clip_item = QTreeWidgetItem(layer_item, [f"    Clip: {clip_obj.name}"])
                    clip_item.setData(0, 1000, clip_obj)
                    clip_item.setFlags(clip_item.flags() | Qt.ItemFlag.ItemIsEditable)
        
        self.tree.itemSelectionChanged.connect(self.on_tree_selection_changed)
        # We don't call filter_tree here anymore to prevent collapse state override
        # The filter is reapplied when text changes or cleared.

    def create_new_segment(self):
        if not self.animation_file: 
            self.log_message("Action failed: No animation data loaded to add a segment to.")
            return
            
        text, ok = QInputDialog.getText(self, 'New Segment', 'Enter a name for the new segment:')
        if ok and text:
            existing_segments = {c.segment for c in self.animation_file.clips}
            if text in existing_segments:
                QMessageBox.warning(self, "Name Conflict", f"A segment named '{text}' already exists.")
                return

            max_order = max((c.order_index for c in self.animation_file.clips), default=-1)
            self.animation_file.clips.append(AnimationClip(
                name="New Animation", segment=text, layer="Main", length=1.0, order_index=max_order + 1))
            self.log_message(f"Created new segment '{text}' with a default clip 'New Animation'.")
            self.populate_animation_tree()

    def delete_selected_items(self):
        selected_items = self.tree.selectedItems()
        if not selected_items: return

        # Identify what to delete
        segments_to_delete = set()
        layers_to_delete = set() # as (segment_name, layer_name) tuples
        clips_to_delete = set()

        for item in selected_items:
            item_type = item.data(0, 1000)
            if item_type == "segment":
                segments_to_delete.add(item.text(0).replace("Segment: ", "").strip())
            elif item_type == "layer":
                layer_name = item.text(0).replace("  Layer: ", "").strip()
                segment_name = item.parent().text(0).replace("Segment: ", "").strip()
                layers_to_delete.add((segment_name, layer_name))
            elif isinstance(item_type, AnimationClip):
                clips_to_delete.add(item_type)

        if not any([segments_to_delete, layers_to_delete, clips_to_delete]):
            return

        reply = QMessageBox.question(self, 'Confirm Deletion',
                                     f"Are you sure you want to delete the selected item(s) and all their contents?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        
        if reply == QMessageBox.StandardButton.Yes:
            initial_clip_count = len(self.animation_file.clips)
            
            # Build a list of clips to keep
            clips_to_keep = []
            for c in self.animation_file.clips:
                if c in clips_to_delete:
                    continue
                if c.segment in segments_to_delete:
                    continue
                if (c.segment, c.layer) in layers_to_delete:
                    continue
                clips_to_keep.append(c)

            self.animation_file.clips = clips_to_keep
            deleted_count = initial_clip_count - len(clips_to_keep)
            self.log_message(f"Deleted {deleted_count} clip(s) as part of deleting selected segments/layers/clips.")
            self.populate_animation_tree()
            
    def rename_selected_item(self):
        if not self.tree.currentItem(): return
        self.tree.editItem(self.tree.currentItem(), 0)

    def on_item_renamed(self, item, column):
        item_type = item.data(0, 1000)
        
        if item_type == "segment":
            old_name = item.text(0).replace("Segment: ", "").strip()
            # This is a placeholder for the real old name, which we need to deduce
            # For simplicity, we find it by looking at the first child's data
            if item.childCount() > 0 and item.child(0).childCount() > 0:
                 old_name = item.child(0).child(0).data(0, 1000).segment
            self.handle_segment_rename(item, old_name)
        elif item_type == "layer":
            self.handle_layer_rename(item)
        elif isinstance(item_type, AnimationClip):
            self.update_clip_name(item_type, item, item.text(0).replace("Clip: ", "").strip())

    def handle_segment_rename(self, item, old_name):
        new_name = item.text(0).replace("Segment: ", "").strip()
        
        self.tree.blockSignals(True)
        if not new_name or new_name == old_name:
            item.setText(0, f"Segment: {old_name}")
            self.tree.blockSignals(False)
            return

        if any(c.segment == new_name for c in self.animation_file.clips):
            QMessageBox.warning(self, "Name Conflict", f"A segment named '{new_name}' already exists.")
            item.setText(0, f"Segment: {old_name}")
            self.tree.blockSignals(False)
            return
            
        for clip in self.animation_file.clips:
            if clip.segment == old_name:
                clip.segment = new_name
                
        self.log_message(f"Renamed segment '{old_name}' to '{new_name}'. All child clips updated.")
        item.setText(0, f"Segment: {new_name}")
        self.tree.blockSignals(False)
        self.populate_animation_tree()

    def handle_layer_rename(self, item):
        new_name = item.text(0).replace("Layer: ", "").replace("  ", "").strip()
        segment_name = item.parent().text(0).replace("Segment: ", "").strip()
        
        old_name = new_name # Placeholder
        if item.childCount() > 0:
            old_name = item.child(0).data(0, 1000).layer
            
        self.tree.blockSignals(True)
        if not new_name or new_name == old_name:
            item.setText(0, f"  Layer: {old_name}")
            self.tree.blockSignals(False)
            return
            
        if any(c.layer == new_name and c.segment == segment_name for c in self.animation_file.clips):
            QMessageBox.warning(self, "Name Conflict", f"A layer named '{new_name}' already exists in this segment.")
            item.setText(0, f"  Layer: {old_name}")
            self.tree.blockSignals(False)
            return
        
        for clip in self.animation_file.clips:
            if clip.segment == segment_name and clip.layer == old_name:
                clip.layer = new_name
        
        self.log_message(f"Renamed layer '{old_name}' to '{new_name}' in segment '{segment_name}'.")
        item.setText(0, f"  Layer: {new_name}")
        self.tree.blockSignals(False)
        self.populate_animation_tree()

    def update_clip_name(self, clip, tree_item, new_name_raw):
        old_name = clip.name
        new_name = new_name_raw.strip()
        
        # This function might be called from the properties panel
        # To avoid re-triggering, block signals on the editor if it's focused
        editor = self.properties_panel.name_edit
        was_blocked = editor.signalsBlocked()
        editor.blockSignals(True)
        
        if not new_name or new_name == old_name:
            editor.setText(old_name)
            editor.blockSignals(was_blocked)
            return
            
        for other_clip in self.animation_file.clips:
            if (other_clip is not clip and
                other_clip.segment == clip.segment and
                other_clip.layer == clip.layer and
                other_clip.name == new_name):
                QMessageBox.warning(self, "Name Conflict", f"A clip named '{new_name}' already exists in this layer.")
                editor.setText(old_name)
                tree_item.setText(0, f"    Clip: {old_name}")
                editor.blockSignals(was_blocked)
                return

        clip.name = new_name
        self.log_message(f"Renamed clip '{old_name}' to '{new_name}'.")

        for other_clip in self.animation_file.clips:
            if other_clip.other_properties.get("NextAnimationName") == old_name:
                if other_clip.layer == clip.layer and other_clip.segment == clip.segment:
                    other_clip.other_properties["NextAnimationName"] = new_name
                    self.log_message(f"Updated NextAnimationName for clip '{other_clip.name}' to '{new_name}'.")

        editor.setText(new_name)
        tree_item.setText(0, f"    Clip: {new_name}")
        editor.blockSignals(was_blocked)

    def duplicate_selected_clip(self):
        item = self.tree.currentItem()
        if not item: return
        clip_obj = item.data(0, 1000)
        if not isinstance(clip_obj, AnimationClip): return

        # Find a unique name for the copy
        base_name = clip_obj.name
        new_name = f"{base_name} (copy)"
        layer_clips = [c for c in self.animation_file.clips if c.segment == clip_obj.segment and c.layer == clip_obj.layer]
        existing_names = {c.name for c in layer_clips}
        
        counter = 2
        while new_name in existing_names:
            new_name = f"{base_name} (copy {counter})"
            counter += 1

        new_clip = copy.deepcopy(clip_obj)
        new_clip.name = new_name
        new_clip.order_index = max((c.order_index for c in self.animation_file.clips), default=-1) + 1
        
        self.animation_file.clips.append(new_clip)
        self.log_message(f"Duplicated clip '{clip_obj.name}' as '{new_name}' in '{new_clip.segment}/{new_clip.layer}'.")
        self.populate_animation_tree()

    def batch_rename_items(self):
        selected_items = [item.data(0, 1000) for item in self.tree.selectedItems() if isinstance(item.data(0, 1000), AnimationClip)]
        if not selected_items:
            QMessageBox.information(self, "Info", "Select one or more clips to rename.")
            return

        dialog = BatchRenameDialog(self)
        if dialog.exec():
            find_text = dialog.find_edit.text()
            replace_text = dialog.replace_edit.text()
            prefix = dialog.prefix_edit.text()
            suffix = dialog.suffix_edit.text()
            
            renamed_count = 0
            for clip in selected_items:
                original_name = clip.name
                new_name = original_name
                
                if find_text:
                    new_name = new_name.replace(find_text, replace_text)
                if prefix:
                    new_name = prefix + new_name
                if suffix:
                    new_name = new_name + suffix
                    
                if new_name != original_name:
                    # Check for conflicts
                    is_conflict = any(c.name == new_name and c.layer == clip.layer and c.segment == clip.segment
                                    for c in self.animation_file.clips if c is not clip)
                    if is_conflict:
                        self.log_message(f"SKIPPED batch rename for '{original_name}' to '{new_name}' due to name conflict.")
                        continue
                    
                    clip.name = new_name
                    # Also update references
                    for other_clip in self.animation_file.clips:
                        if other_clip.other_properties.get("NextAnimationName") == original_name and \
                           other_clip.layer == clip.layer and other_clip.segment == clip.segment:
                           other_clip.other_properties["NextAnimationName"] = new_name
                           
                    renamed_count += 1
            
            self.log_message(f"Batch renamed {renamed_count} clip(s).")
            self.populate_animation_tree()

class BatchRenameDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Batch Rename Clips")
        layout = QFormLayout(self)
        
        self.find_edit = QLineEdit()
        self.replace_edit = QLineEdit()
        self.prefix_edit = QLineEdit()
        self.suffix_edit = QLineEdit()
        
        layout.addRow("Find text:", self.find_edit)
        layout.addRow("Replace with:", self.replace_edit)
        layout.addRow(QLabel("--- OR ---"))
        layout.addRow("Add Prefix:", self.prefix_edit)
        layout.addRow("Add Suffix:", self.suffix_edit)
        
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

# --- 5. Application Entry Point ---
if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())