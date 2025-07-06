import sys
import os
import json
import copy
import struct
from collections import defaultdict
import math

# Using PyQt6 for the GUI
from PyQt6.QtCore import Qt, QMimeData, QDateTime, QSettings
from PyQt6.QtGui import QAction, QIcon, QDrag
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTreeWidget, QTreeWidgetItem, QAbstractItemView, QFileDialog, QLabel,
    QMenu, QMessageBox, QInputDialog, QToolBar, QFormLayout, QLineEdit,
    QListWidget, QPlainTextEdit, QPushButton, QDialog, QDialogButtonBox,
    QRadioButton, QStyle
)

DARK_STYLE = """
    QWidget {
        background-color: #2e2e2e;
        color: #e0e0e0;
        font-size: 10pt;
    }
    QMainWindow {
        background-color: #2e2e2e;
    }
    QTreeWidget {
        background-color: #252525;
        color: #e0e0e0;
        border: 1px solid #444;
    }
    QTreeWidget::item:selected {
        background-color: #0078d7;
        color: white;
    }
    QTreeWidget::item:hover {
        background-color: #3e3e3e;
    }
    QHeaderView::section {
        background-color: #3e3e3e;
        color: #e0e0e0;
        padding: 4px;
        border: 1px solid #555;
    }
    QLineEdit, QListWidget, QPlainTextEdit {
        background-color: #3e3e3e;
        color: #e0e0e0;
        border: 1px solid #555;
        border-radius: 3px;
        padding: 2px;
    }
    QPushButton, QDialogButtonBox > QPushButton {
        background-color: #4a4a4a;
        color: #e0e0e0;
        border: 1px solid #555;
        padding: 5px;
        border-radius: 3px;
    }
    QPushButton:hover, QDialogButtonBox > QPushButton:hover {
        background-color: #5a5a5a;
    }
    QPushButton:pressed, QDialogButtonBox > QPushButton:pressed {
        background-color: #6a6a6a;
    }
    QMenuBar, QMenu {
        background-color: #2e2e2e;
        color: #e0e0e0;
    }
    QMenuBar::item:selected, QMenu::item:selected {
        background-color: #0078d7;
    }
    QToolBar {
        background-color: #2e2e2e;
        border: none;
    }
    QDialog {
         background-color: #2e2e2e;
    }
    QPlainTextEdit#LogConsole {
        background-color: #212121;
        color: #d0d0d0;
        font-family: Consolas, monospace;
        border: 1px solid #444;
    }
"""

# --- 1. Keyframe Encoding/Decoding Logic ---

class KeyframeEncoder:
    """
    Replicates the keyframe encoding logic from AtomAnimationSerializer.cs.
    """
    @staticmethod
    def encode_keyframe(time: float, value: float, curve_type: int, last_v: float, last_c: int) -> str:
        """Encodes a single keyframe into the plugin's string format."""
        sb = []
        has_value = abs(last_v - value) > 1e-7
        has_curve_type = last_c != curve_type
        encoded_value = 0
        if has_value: encoded_value |= (1 << 0)
        if has_curve_type: encoded_value |= (1 << 1)
        sb.append(chr(ord('A') + encoded_value))
        sb.append(struct.pack('<f', time).hex().upper())
        if has_value:
            sb.append(struct.pack('<f', value).hex().upper())
        if has_curve_type:
            sb.append(struct.pack('<B', curve_type).hex().upper())
        return "".join(sb)

class KeyframeDecoder:
    """
    Replicates the keyframe decoding logic from AtomAnimationSerializer.cs.
    """
    @staticmethod
    def decode_keyframe(encoded_str: str, last_v: float, last_c: int) -> tuple[float, float, int]:
        """Decodes a single keyframe from the plugin's string format."""
        if not encoded_str: raise ValueError("Encoded string is empty")
        flag_char = encoded_str[0]
        encoded_value = ord(flag_char) - ord('A')
        has_value = (encoded_value & (1 << 0)) != 0
        has_curve_type = (encoded_value & (1 << 1)) != 0
        ptr = 1
        time_hex = encoded_str[ptr:ptr+8]
        time = struct.unpack('<f', bytes.fromhex(time_hex))[0]
        ptr += 8
        value = last_v
        if has_value:
            value_hex = encoded_str[ptr:ptr+8]
            value = struct.unpack('<f', bytes.fromhex(value_hex))[0]
            ptr += 8
        curve_type = last_c
        if has_curve_type:
            curve_type_hex = encoded_str[ptr:ptr+2]
            curve_type = struct.unpack('<B', bytes.fromhex(curve_type_hex))[0]
            ptr += 2
        return time, value, curve_type

# --- 2. Data Model ---

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

class AnimationClip:
    def __init__(self, name, segment, layer, length, order_index=0, **kwargs):
        self.name, self.segment, self.layer, self.length, self.order_index = name, segment, layer, length, order_index
        self.other_properties, self.float_params, self.controllers = kwargs, [], []
    @classmethod
    def from_dict(cls, data):
        known_keys = {"AnimationName", "AnimationSegment", "AnimationLayer", "AnimationLength", "FloatParams", "Controllers", "OrderIndex"}
        instance = cls(name=data.get("AnimationName", "Unnamed"), segment=data.get("AnimationSegment", "Default"), layer=data.get("AnimationLayer", "Default"), length=float(data.get("AnimationLength", 0.0)), order_index=int(data.get("OrderIndex", 0)), **{k: v for k, v in data.items() if k not in known_keys})
        if "FloatParams" in data: instance.float_params = [FloatParameter.from_dict(p) for p in data["FloatParams"]]
        if "Controllers" in data: instance.controllers = [ControllerTarget.from_dict(c) for c in data["Controllers"]]
        return instance
    def to_dict(self):
        data = {"AnimationName": self.name, "AnimationSegment": self.segment, "AnimationLayer": self.layer, "AnimationLength": str(self.length)}; data.update(self.other_properties)
        if self.float_params: data["FloatParams"] = [p.to_dict() for p in sorted(self.float_params, key=lambda p: (p.storable, p.name))]
        if self.controllers: data["Controllers"] = [c.to_dict() for c in sorted(self.controllers, key=lambda c: c.id)]
        return data

class AnimationFile:
    def __init__(self, version, atom_type):
        self.version, self.atom_type, self.clips = version, atom_type, []
    @classmethod
    def from_dict(cls, data):
        instance = cls(data.get("SerializeVersion"), data.get("AtomType"))
        if "Clips" in data:
            for i, clip_data in enumerate(data["Clips"]): clip_data['OrderIndex'] = i
            instance.clips = [AnimationClip.from_dict(c) for c in data["Clips"]]
        return instance
    def to_dict(self):
        self.clips.sort(key=lambda c: c.order_index)
        return {"SerializeVersion": self.version, "AtomType": self.atom_type, "Clips": [c.to_dict() for c in self.clips]}

# --- 3. Custom UI Components ---

class AnimationTreeWidget(QTreeWidget):
    def __init__(self, parent_window):
        super().__init__(); self.parent_window = parent_window
        self.setDragEnabled(True); self.setAcceptDrops(True); self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.open_context_menu)
        self.itemDoubleClicked.connect(self.on_item_double_clicked)
    def on_item_double_clicked(self, item, column): self.parent_window.rename_selected_item()
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_F2: self.parent_window.rename_selected_item()
        elif event.key() == Qt.Key.Key_Delete: self.parent_window.delete_selected_items()
        else: super().keyPressEvent(event)
    def dragEnterEvent(self, event):
        if event.source() == self and event.mimeData().text() in ["clip-drag", "layer-drag"]: event.acceptProposedAction()
        else: super().dragEnterEvent(event)
    def dragMoveEvent(self, event):
        if event.source() == self and event.mimeData().text() in ["clip-drag", "layer-drag"]: event.acceptProposedAction()
        else: super().dragMoveEvent(event)
    def startDrag(self, supportedActions):
        items = self.selectedItems();
        if not items: return
        item, drag, mime_data = items[0], QDrag(self), QMimeData()
        if item.parent() and not item.parent().parent():
            if len(items) > 1: return
            mime_data.setText("layer-drag"); drag.setMimeData(mime_data); drag.exec(Qt.DropAction.MoveAction)
        elif item.parent() and item.parent().parent():
            mime_data.setText("clip-drag"); drag.setMimeData(mime_data); drag.exec(Qt.DropAction.MoveAction | Qt.DropAction.CopyAction, Qt.DropAction.MoveAction)
    def dropEvent(self, event):
        mime_text = event.mimeData().text()
        if mime_text == "clip-drag": self.handle_clip_drop(event)
        elif mime_text == "layer-drag": self.handle_layer_merge(event)
        else: event.ignore()
    def get_layer_clips(self, segment_name, layer_name): return [c for c in self.parent_window.animation_file.clips if c.segment == segment_name and c.layer == layer_name]
    def get_layer_target_signature(self, segment_name, layer_name, anim_file):
        clips = [c for c in anim_file.clips if c.segment == segment_name and c.layer == layer_name]
        if not clips: return frozenset(), frozenset()
        fp_keys = {(p.storable, p.name) for c in clips for p in c.float_params}; c_ids = {c.id for clip in clips for c in clip.controllers}
        return frozenset(fp_keys), frozenset(c_ids)
    def handle_layer_merge(self, event):
        source_item, target_item_at_point = self.selectedItems()[0], self.itemAt(event.position().toPoint())
        target_layer_item = None
        if target_item_at_point:
            if target_item_at_point.parent() and not target_item_at_point.parent().parent(): target_layer_item = target_item_at_point
            elif target_item_at_point.parent() and target_item_at_point.parent().parent(): target_layer_item = target_item_at_point.parent()
        if not target_layer_item or source_item == target_layer_item: event.ignore(); return
        if source_item.parent() != target_layer_item.parent(): QMessageBox.warning(self, "Invalid Operation", "Layers can only be merged within the same segment."); return
        reply = QMessageBox.question(self, 'Confirm Layer Merge', f"Merge '{source_item.text(0).strip()}' into '{target_layer_item.text(0).strip()}'?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.No: event.ignore(); return
        seg_name = target_layer_item.parent().text(0).replace("Segment: ", "").strip(); src_layer_name = source_item.text(0).replace("  Layer: ", "").strip(); tgt_layer_name = target_layer_item.text(0).replace("  Layer: ", "").strip()
        self.parent_window.log_message(f"Merging layer '{src_layer_name}' into '{tgt_layer_name}' in segment '{seg_name}'.")
        src_clips, tgt_clips = self.get_layer_clips(seg_name, src_layer_name), self.get_layer_clips(seg_name, tgt_layer_name)
        master_fp = {(p.storable, p.name): p for clip in src_clips + tgt_clips for p in clip.float_params}
        master_c = {c.id: c for clip in src_clips + tgt_clips for c in clip.controllers}
        tgt_clips_by_name = {clip.name: clip for clip in tgt_clips}
        for src_clip in src_clips:
            if src_clip.name in tgt_clips_by_name:
                tgt_clip = tgt_clips_by_name[src_clip.name]
                existing_fp = {(p.storable, p.name) for p in tgt_clip.float_params}; existing_c = {c.id for c in tgt_clip.controllers}
                for param in src_clip.float_params:
                    if (param.storable, param.name) not in existing_fp: tgt_clip.float_params.append(param)
                for controller in src_clip.controllers:
                    if controller.id not in existing_c: tgt_clip.controllers.append(controller)
                self.parent_window.animation_file.clips.remove(src_clip)
            else: src_clip.layer = tgt_layer_name
        final_tgt_clips = self.get_layer_clips(seg_name, tgt_layer_name)
        for clip in final_tgt_clips:
            clip_fp_keys = {(p.storable, p.name) for p in clip.float_params}
            for key, t_param in master_fp.items():
                if key not in clip_fp_keys:
                    kf1, kf2 = KeyframeEncoder.encode_keyframe(0.0, 0.0, 3, 0.0, -1), KeyframeEncoder.encode_keyframe(clip.length, 0.0, 3, 0.0, 3)
                    clip.float_params.append(FloatParameter(t_param.storable, t_param.name, [kf1, kf2], t_param.min, t_param.max))
            clip_c_ids = {c.id for c in clip.controllers}
            for c_id, t_ctrl in master_c.items():
                if c_id not in clip_c_ids:
                    new_c = ControllerTarget(c_id, **copy.deepcopy(t_ctrl.properties))
                    for axis in ['X','Y','Z']: kf1, kf2 = KeyframeEncoder.encode_keyframe(0.0,0.0,3,0.0,-1),KeyframeEncoder.encode_keyframe(clip.length,0.0,3,0.0,3); new_c.properties[axis] = [kf1, kf2]
                    for axis in ['RotX','RotY','RotZ']: kf1, kf2 = KeyframeEncoder.encode_keyframe(0.0,0.0,3,0.0,-1),KeyframeEncoder.encode_keyframe(clip.length,0.0,3,0.0,3); new_c.properties[axis] = [kf1, kf2]
                    kf1w, kf2w = KeyframeEncoder.encode_keyframe(0.0,1.0,3,0.0,-1),KeyframeEncoder.encode_keyframe(clip.length,1.0,3,1.0,3); new_c.properties['RotW'] = [kf1w, kf2w]
                    clip.controllers.append(new_c)
        self.parent_window.log_message("Layer merge complete."); self.parent_window.populate_animation_tree(); event.acceptProposedAction()
    def handle_clip_drop(self, event):
        source_items, target_item = self.selectedItems(), self.itemAt(event.position().toPoint())
        if not source_items or not target_item: event.ignore(); return
        is_copy, source_layer_item = event.proposedAction() == Qt.DropAction.CopyAction, source_items[0].parent()
        target_layer_item = None
        if isinstance(target_item.data(0, 1000), AnimationClip): target_layer_item = target_item.parent()
        elif target_item.childCount() > 0 and isinstance(target_item.child(0).data(0, 1000), AnimationClip): target_layer_item = target_item
        if not target_layer_item: event.ignore(); return
        if not is_copy and source_layer_item == target_layer_item: self.reorder_clips_in_layer(source_items, target_item, event)
        else: self.move_or_copy_clips_to_layer(source_items, target_layer_item, is_copy, event)
        self.parent_window.populate_animation_tree(); event.acceptProposedAction()
    def reorder_clips_in_layer(self, source_items, target_item, event):
        layer_item = source_items[0].parent(); seg_name = layer_item.parent().text(0).replace("Segment: ", "").strip(); layer_name = layer_item.text(0).replace("  Layer: ", "").strip()
        clips_in_layer = sorted(self.get_layer_clips(seg_name, layer_name), key=lambda c: c.order_index)
        dragged_clips = [item.data(0, 1000) for item in source_items]; remaining_clips = [c for c in clips_in_layer if c not in dragged_clips]
        drop_pos, target_clip = self.dropIndicatorPosition(), target_item.data(0, 1000) if isinstance(target_item.data(0, 1000), AnimationClip) else None
        if target_clip and target_clip in remaining_clips:
            target_idx = remaining_clips.index(target_clip)
            if drop_pos == QAbstractItemView.DropIndicatorPosition.BelowItem: target_idx += 1
        else: target_idx = len(remaining_clips)
        for clip in reversed(dragged_clips): remaining_clips.insert(target_idx, clip)
        for i, clip in enumerate(remaining_clips): clip.order_index = i
        self.parent_window.log_message(f"Reordered {len(dragged_clips)} clip(s) in '{layer_name}'.")
    def move_or_copy_clips_to_layer(self, source_items, target_layer_item, is_copy, event):
        src_layer, src_seg = source_items[0].parent().text(0).replace("  Layer: ","").strip(), source_items[0].parent().parent().text(0).replace("Segment: ","").strip()
        tgt_layer, tgt_seg = target_layer_item.text(0).replace("  Layer: ","").strip(), target_layer_item.parent().text(0).replace("Segment: ","").strip()
        src_fp_sig, src_c_sig = self.get_layer_target_signature(src_seg, src_layer, self.parent_window.animation_file)
        if src_seg != tgt_seg:
            compat_layer = None
            for i in range(target_layer_item.parent().childCount()):
                layer_item = target_layer_item.parent().child(i); layer_name = layer_item.text(0).replace("  Layer: ","").strip()
                fp_sig, c_sig = self.get_layer_target_signature(tgt_seg, layer_name, self.parent_window.animation_file)
                if fp_sig == src_fp_sig and c_sig == src_c_sig: compat_layer = layer_name; break
            if compat_layer: tgt_layer = compat_layer
            else:
                new_layer, existing_layers, counter = src_layer, {target_layer_item.parent().child(i).text(0).replace("  Layer: ","").strip() for i in range(target_layer_item.parent().childCount())}, 1
                while new_layer in existing_layers: new_layer = f"{src_layer}_{counter}"; counter += 1
                tgt_layer = new_layer; self.parent_window.log_message(f"Created new layer '{tgt_layer}' in '{tgt_seg}'.")
        clips_in_tgt = self.get_layer_clips(tgt_seg, tgt_layer); existing_names = {c.name for c in clips_in_tgt}; max_order = max((c.order_index for c in clips_in_tgt), default=-1)
        for src_item in source_items:
            src_clip = src_item.data(0, 1000); new_name = src_clip.name
            if new_name in existing_names:
                reply = QMessageBox.question(self, "Name Conflict", f"Clip '{new_name}' exists. Replace?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if reply == QMessageBox.StandardButton.Yes:
                    to_remove = next(c for c in clips_in_tgt if c.name == new_name); self.parent_window.animation_file.clips.remove(to_remove)
                else: self.parent_window.log_message(f"Skipped {'copy' if is_copy else 'move'} of '{new_name}'."); continue
            max_order += 1
            if is_copy:
                new_clip = copy.deepcopy(src_clip); new_clip.name, new_clip.segment, new_clip.layer, new_clip.order_index = new_name, tgt_seg, tgt_layer, max_order
                self.parent_window.animation_file.clips.append(new_clip); existing_names.add(new_name); self.parent_window.log_message(f"Copied '{src_clip.name}' to '{tgt_seg}/{tgt_layer}'.")
            else:
                src_clip.name, src_clip.segment, src_clip.layer, src_clip.order_index = new_name, tgt_seg, tgt_layer, max_order; self.parent_window.log_message(f"Moved '{src_clip.name}' to '{tgt_seg}/{tgt_layer}'.")
    def open_context_menu(self, position):
        menu, selected = QMenu(self), self.selectedItems()
        if selected:
            if len(selected) == 1:
                item = selected[0]
                rename = menu.addAction("Rename..."); rename.setShortcut("F2"); rename.triggered.connect(self.parent_window.rename_selected_item)
                if item.parent() and item.parent().parent():
                    duplicate = menu.addAction("Duplicate Clip"); duplicate.setShortcut("Ctrl+D"); duplicate.triggered.connect(self.parent_window.duplicate_selected_clip)
            delete = menu.addAction(QIcon.fromTheme("edit-delete"), f"Delete {len(selected)} item(s)"); delete.setShortcut("Delete"); delete.triggered.connect(self.parent_window.delete_selected_items)
        if not menu.isEmpty(): menu.exec(self.viewport().mapToGlobal(position))

class ClipPropertiesPanel(QWidget):
    def __init__(self, main_window):
        super().__init__(); self.main_window = main_window; self.clip, self.current_tree_item = None, None; self.init_ui(); self.clear()
    def init_ui(self):
        self.layout = QVBoxLayout(self); self.form_layout = QFormLayout(); self.name_edit = QLineEdit(); self.name_edit.editingFinished.connect(self.on_name_changed); self.form_layout.addRow("Name:", self.name_edit); self.layout.addLayout(self.form_layout)
        self.layout.addWidget(QLabel("<b>General</b>")); self.general_form_layout = QFormLayout(); self.segment_label, self.layer_label, self.length_label, self.loop_label = QLabel(), QLabel(), QLabel(), QLabel()
        self.general_form_layout.addRow("Segment:", self.segment_label); self.general_form_layout.addRow("Layer:", self.layer_label); self.general_form_layout.addRow("Length:", self.length_label); self.general_form_layout.addRow("Loop:", self.loop_label); self.layout.addLayout(self.general_form_layout)
        self.layout.addWidget(QLabel("<b>Sequencing</b>")); self.sequence_form_layout = QFormLayout(); self.next_anim_label = QLabel(); self.sequence_form_layout.addRow("Next Animation:", self.next_anim_label); self.layout.addLayout(self.sequence_form_layout)
        self.layout.addWidget(QLabel("<b>Targets</b>")); self.targets_list = QListWidget(); self.layout.addWidget(self.targets_list); self.layout.addStretch()
    def display_clip_properties(self, clip, item):
        self.clip, self.current_tree_item = clip, item; self.name_edit.blockSignals(True); self.name_edit.setText(clip.name); self.name_edit.blockSignals(False)
        self.segment_label.setText(clip.segment); self.layer_label.setText(clip.layer); self.length_label.setText(f"{clip.length:.3f}s")
        self.loop_label.setText("Yes" if clip.other_properties.get('Loop', '0') == '1' else "No"); self.next_anim_label.setText(clip.other_properties.get('NextAnimationName', 'None')); self.targets_list.clear()
        targets = [f"[C] {c.id}" for c in clip.controllers] + [f"[F] {p.storable}/{p.name}" for p in clip.float_params]
        if targets: self.targets_list.addItems(sorted(targets))
        else: self.targets_list.addItem("No targets in this clip.")
        self.show()
    def on_name_changed(self):
        if self.clip: self.main_window.update_clip_name(self.clip, self.current_tree_item, self.name_edit.text())
    def clear(self): self.clip, self.current_tree_item = None, None; self.hide()

# --- 4. Main Application Window ---
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__(); self.animation_file, self.current_file_path = None, None; self.setWindowTitle("Timeliner")
        ico_path = os.path.join(getattr(sys, '_MEIPASS', os.path.abspath('.')), 'timeliner-logo.ico')
        if os.path.exists(ico_path): self.setWindowIcon(QIcon(ico_path))
        self.setGeometry(100, 100, 1200, 800); self.settings = QSettings("VamTimelineTools", "TimelinerEditor"); self.last_directory = self.settings.value("last_directory", "")
        self.init_ui()
        self.last_center_root_delta_xz = (0.0, 0.0)

    def init_ui(self):
        self.open_action=QAction("&Open...",self);self.open_action.triggered.connect(self.open_file)
        self.save_as_action=QAction("&Save As...",self);self.save_as_action.triggered.connect(self.save_file_as)
        exit_action=QAction("E&xit",self);exit_action.triggered.connect(self.close)
        self.new_segment_action=QAction("New &Segment...",self);self.new_segment_action.triggered.connect(self.create_new_segment)
        rename_action=QAction("Re&name...",self);rename_action.setShortcut("F2");rename_action.triggered.connect(self.rename_selected_item)
        batch_rename_action=QAction("Change Names in &Batch...",self);batch_rename_action.triggered.connect(self.batch_rename_items)
        self.delete_action=QAction("&Delete Selected",self);self.delete_action.setShortcut("Delete");self.delete_action.triggered.connect(self.delete_selected_items)
        duplicate_action=QAction("&Duplicate Clip",self);duplicate_action.setShortcut("Ctrl+D");duplicate_action.triggered.connect(self.duplicate_selected_clip)
        center_root_action=QAction("Center &Root on First Frame",self);center_root_action.triggered.connect(self.center_root_on_first_frame)
        move_by_offset_action = QAction("Move by &Offset...", self); move_by_offset_action.triggered.connect(self.move_root_by_offset)
        self.dark_mode_action = QAction("&Dark Mode", self); self.dark_mode_action.setCheckable(True); self.dark_mode_action.toggled.connect(self.toggle_dark_mode)

        menu_bar=self.menuBar()
        file_menu=menu_bar.addMenu("&File");file_menu.addAction(self.open_action);file_menu.addAction(self.save_as_action);file_menu.addSeparator();file_menu.addAction(self.new_segment_action);file_menu.addSeparator();file_menu.addAction(exit_action)
        edit_menu=menu_bar.addMenu("&Edit");edit_menu.addAction(rename_action);edit_menu.addAction(batch_rename_action);edit_menu.addAction(duplicate_action);edit_menu.addSeparator();edit_menu.addAction(center_root_action);edit_menu.addAction(move_by_offset_action);edit_menu.addSeparator();edit_menu.addAction(self.delete_action)
        view_menu=menu_bar.addMenu("&View"); view_menu.addAction(self.dark_mode_action)
        
        toolbar=self.addToolBar("Main Toolbar");toolbar.addAction(self.open_action);toolbar.addAction(self.save_as_action);toolbar.addSeparator();toolbar.addAction(self.new_segment_action);toolbar.addAction(self.delete_action)
        
        main_widget=QWidget();self.setCentralWidget(main_widget);main_layout=QHBoxLayout(main_widget)
        left_panel=QWidget();left_layout=QVBoxLayout(left_panel);left_panel.setFixedWidth(400);filter_layout=QHBoxLayout();self.filter_edit=QLineEdit();self.filter_edit.setPlaceholderText("Filter animations...");self.filter_edit.textChanged.connect(self.filter_tree);filter_layout.addWidget(self.filter_edit)
        self.fold_all_button=QPushButton("Fold All");self.fold_all_button.clicked.connect(self.fold_all_items);filter_layout.addWidget(self.fold_all_button)
        self.unfold_all_button=QPushButton("Unfold All");self.unfold_all_button.clicked.connect(self.unfold_all_items);filter_layout.addWidget(self.unfold_all_button)
        left_layout.addLayout(filter_layout)
        self.tree=AnimationTreeWidget(self);self.tree.setHeaderLabels(["Segment / Layer / Animation"]);self.tree.itemSelectionChanged.connect(self.on_tree_selection_changed);self.tree.itemChanged.connect(self.on_item_renamed);left_layout.addWidget(self.tree)
        right_panel=QWidget();right_layout=QVBoxLayout(right_panel)
        self.placeholder_label=QLabel("Select a clip to see its properties.");self.placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.properties_panel=ClipPropertiesPanel(self)
        self.log_console=QPlainTextEdit();self.log_console.setReadOnly(True);self.log_console.setFixedHeight(150);self.log_console.setObjectName("LogConsole")
        right_layout.addWidget(self.placeholder_label);right_layout.addWidget(self.properties_panel);right_layout.addStretch(1);right_layout.addWidget(QLabel("<b>Console Log</b>"));right_layout.addWidget(self.log_console)
        main_layout.addWidget(left_panel);main_layout.addWidget(right_panel)
        self.log_message("Application started.")
        
        is_dark = self.settings.value("darkModeEnabled", False, type=bool)
        self.dark_mode_action.setChecked(is_dark)
        self.apply_styles(is_dark)

    def toggle_dark_mode(self, checked):
        self.settings.setValue("darkModeEnabled", checked)
        self.apply_styles(checked)
        self.log_message(f"Dark Mode {'Enabled' if checked else 'Disabled'}.")

    def apply_styles(self, is_dark):
        if is_dark:
            app.setStyleSheet(DARK_STYLE)
        else:
            app.setStyleSheet("")
        self.update_toolbar_icons()

    def update_toolbar_icons(self):
        style = self.style()
        self.open_action.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton))
        self.save_as_action.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        self.new_segment_action.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_FileDialogNewFolder))
        self.delete_action.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_TrashIcon))

    def get_tree_collapse_state(self):
        state = set()
        for i in range(self.tree.topLevelItemCount()):
            seg_item = self.tree.topLevelItem(i); seg_key = seg_item.text(0)
            if not seg_item.isExpanded(): state.add(seg_key)
            else:
                for j in range(seg_item.childCount()):
                    layer_item = seg_item.child(j)
                    if not layer_item.isExpanded(): state.add(f"{seg_key}::{layer_item.text(0)}")
        return state

    def fold_all_items(self):
        if self.tree: self.tree.collapseAll(); self.log_message("All items folded.")
    def unfold_all_items(self):
        if self.tree: self.tree.expandAll(); self.log_message("All items unfolded.")
    def log_message(self, message):
        timestamp = QDateTime.currentDateTime().toString("hh:mm:ss"); self.log_console.appendPlainText(f"[{timestamp}] {message}")

    def on_tree_selection_changed(self):
        selected = self.tree.selectedItems()
        if selected and isinstance(selected[0].data(0, 1000), AnimationClip):
            self.properties_panel.display_clip_properties(selected[0].data(0, 1000), selected[0]); self.placeholder_label.hide()
        else: self.properties_panel.clear(); self.placeholder_label.show()

    def filter_tree(self, text):
        search_text = text.lower(); is_filtering = bool(search_text)
        if self.tree.topLevelItemCount() == 0: return
        if is_filtering:
            for i in range(self.tree.topLevelItemCount()):
                seg_item = self.tree.topLevelItem(i); seg_visible = False
                for j in range(seg_item.childCount()):
                    layer_item = seg_item.child(j); layer_visible = False
                    for k in range(layer_item.childCount()):
                        clip_item = layer_item.child(k)
                        if search_text in clip_item.text(0).lower(): clip_item.setHidden(False); layer_visible = True
                        else: clip_item.setHidden(True)
                    if layer_visible or search_text in layer_item.text(0).lower(): layer_item.setHidden(False); seg_visible = True; layer_item.setExpanded(True)
                    else: layer_item.setHidden(True)
                if seg_visible or search_text in seg_item.text(0).lower(): seg_item.setHidden(False); seg_item.setExpanded(True)
                else: seg_item.setHidden(True)
        else: self.populate_animation_tree()

    def open_file(self):
        file_name, _ = QFileDialog.getOpenFileName(self, "Open Animation File", self.last_directory, "JSON Files (*.json)")
        if not file_name: return
        if self.animation_file:
            msg_box = QMessageBox(self); msg_box.setIcon(QMessageBox.Icon.Question); msg_box.setText("File is already open."); msg_box.setInformativeText("Merge or Replace?")
            merge_btn, replace_btn = msg_box.addButton("Merge", QMessageBox.ButtonRole.ActionRole), msg_box.addButton("Replace", QMessageBox.ButtonRole.ActionRole)
            msg_box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole); msg_box.exec()
            if msg_box.clickedButton() == merge_btn: self.merge_animation_files(file_name)
            elif msg_box.clickedButton() == replace_btn: self.load_file(file_name, is_first_load=True)
            else: return
        else: self.load_file(file_name, is_first_load=True)
            
    def load_file(self, file_name, is_first_load=False):
        try:
            with open(file_name, 'r', encoding='utf-8') as f: data = json.load(f)
            self.animation_file = AnimationFile.from_dict(data); self.current_file_path = file_name
            self.populate_animation_tree(is_first_load=is_first_load); self.setWindowTitle(f"Timeliner - {file_name}"); self.log_message(f"Loaded: {file_name}")
            self.last_directory = os.path.dirname(file_name); self.settings.setValue("last_directory", self.last_directory)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error loading file: {e}"); self.log_message(f"ERROR loading '{file_name}': {e}")
            self.animation_file = None; self.current_file_path = None; self.tree.clear()

    def merge_animation_files(self, source_path):
        self.log_message(f"Merging: {source_path}")
        try:
            with open(source_path, 'r', encoding='utf-8') as f: source_data = json.load(f)
            source_anim = AnimationFile.from_dict(source_data)
        except Exception as e: QMessageBox.critical(self, "Error", f"Read failed: {e}"); self.log_message(f"ERROR: Merge failed: {e}"); return
        if self.animation_file.atom_type != source_anim.atom_type:
            QMessageBox.critical(self, "Merge Error", f"Mismatched Atom Types.\nCurrent: {self.animation_file.atom_type}\nSource: {source_anim.atom_type}"); self.log_message("ERROR: Merge failed. Mismatched AtomType."); return
        conflict_dialog = MergeConflictDialog(self);
        if not conflict_dialog.exec(): self.log_message("Merge cancelled."); return
        strategy = conflict_dialog.get_selected_strategy()
        self.log_message(f"Merge strategy: {strategy}")
        added_count = 0; source_grouped = defaultdict(lambda: defaultdict(list))
        for clip in source_anim.clips: source_grouped[clip.segment][clip.layer].append(clip)
        max_order = max((c.order_index for c in self.animation_file.clips), default=-1)
        for seg, layers in source_grouped.items():
            for layer, clips in layers.items():
                s_fp_sig, s_c_sig = self.tree.get_layer_target_signature(seg, layer, source_anim)
                target_seg, target_layer = seg, layer
                target_layers_in_seg = {c.layer for c in self.animation_file.clips if c.segment == target_seg}
                compat_found = False
                for existing_layer in target_layers_in_seg:
                    fp_sig, c_sig = self.tree.get_layer_target_signature(target_seg, existing_layer, self.animation_file)
                    if fp_sig == s_fp_sig and c_sig == s_c_sig: target_layer, compat_found = existing_layer, True; break
                if not compat_found:
                    counter = 1; new_layer = layer
                    while new_layer in target_layers_in_seg: new_layer = f"{layer}_{counter}"; counter += 1
                    target_layer = new_layer; self.log_message(f"Created new layer '{target_layer}' in '{target_seg}'.")
                existing_names = {c.name for c in self.animation_file.clips if c.segment==target_seg and c.layer==target_layer}
                for clip in clips:
                    is_conflict = clip.name in existing_names
                    if is_conflict and strategy == "skip": self.log_message(f"Skipping '{clip.name}' due to conflict."); continue
                    new_clip = copy.deepcopy(clip); new_clip.segment, new_clip.layer = target_seg, target_layer
                    if is_conflict and strategy == "replace":
                        to_remove = next(c for c in self.animation_file.clips if c.segment==target_seg and c.layer==target_layer and c.name==clip.name)
                        self.animation_file.clips.remove(to_remove); self.log_message(f"Replacing clip '{clip.name}'.")
                    elif is_conflict and strategy == "rename":
                        base, i = clip.name, 1; new_name = f"{base}_merged"
                        while new_name in existing_names: new_name = f"{base}_merged_{i}"; i += 1
                        new_clip.name = new_name; self.log_message(f"Renaming '{clip.name}' to '{new_clip.name}'.")
                    max_order += 1; new_clip.order_index = max_order
                    self.animation_file.clips.append(new_clip); existing_names.add(new_clip.name); added_count += 1
        
        self.log_message(f"Merge complete. Added {added_count} clip(s).")
        if self.current_file_path: self.setWindowTitle(self.windowTitle() + " *"); self.current_file_path = None
        self.populate_animation_tree()

    def save_file_as(self):
        if not self.animation_file: self.log_message("Save cancelled: No data loaded."); return
        start_path = self.last_directory or self.current_file_path or ""
        file_name, _ = QFileDialog.getSaveFileName(self, "Save As", start_path, "JSON Files (*.json)")
        if file_name:
            if not file_name.lower().endswith('.json'): file_name += '.json'
            try:
                with open(file_name, 'w', encoding='utf-8') as f: json.dump(self.animation_file.to_dict(), f, indent=3, ensure_ascii=False)
                self.current_file_path = file_name; self.setWindowTitle(f"Timeliner - {file_name}"); self.log_message(f"File saved: {file_name}")
                self.last_directory = os.path.dirname(file_name); self.settings.setValue("last_directory", self.last_directory)
            except Exception as e: QMessageBox.critical(self, "Error", f"Save failed: {e}"); self.log_message(f"ERROR: Save failed. Reason: {e}")

    def populate_animation_tree(self, is_first_load=False):
        self.tree.itemSelectionChanged.disconnect(self.on_tree_selection_changed)
        collapse_state = {} if is_first_load else self.get_tree_collapse_state()
        self.tree.clear();
        if not self.animation_file: self.tree.itemSelectionChanged.connect(self.on_tree_selection_changed); return
        grouped = defaultdict(lambda: defaultdict(list))
        for clip in self.animation_file.clips: grouped[clip.segment][clip.layer].append(clip)
        for seg_name in sorted(grouped.keys()):
            layers = grouped[seg_name]; seg_item = QTreeWidgetItem(self.tree, [f"Segment: {seg_name}"]); seg_item.setData(0, 1000, "segment")
            seg_item.setFlags(seg_item.flags() | Qt.ItemFlag.ItemIsEditable); seg_key = seg_item.text(0); seg_item.setExpanded(seg_key not in collapse_state)
            for layer_name in sorted(layers.keys()):
                clips = layers[layer_name]; layer_item = QTreeWidgetItem(seg_item, [f"  Layer: {layer_name}"]); layer_item.setData(0, 1000, "layer")
                layer_item.setFlags(layer_item.flags() | Qt.ItemFlag.ItemIsEditable); layer_key = f"{seg_key}::{layer_item.text(0)}"; layer_item.setExpanded(layer_key not in collapse_state)
                clips.sort(key=lambda c: c.order_index)
                for clip_obj in clips:
                    clip_item = QTreeWidgetItem(layer_item, [f"    Clip: {clip_obj.name}"]); clip_item.setData(0, 1000, clip_obj); clip_item.setFlags(clip_item.flags() | Qt.ItemFlag.ItemIsEditable)
        self.tree.itemSelectionChanged.connect(self.on_tree_selection_changed)

    def create_new_segment(self):
        if not self.animation_file: self.log_message("Action failed: No data loaded."); return
        text, ok = QInputDialog.getText(self, 'New Segment', 'Enter segment name:')
        if ok and text:
            if any(c.segment == text for c in self.animation_file.clips): QMessageBox.warning(self, "Name Conflict", f"Segment '{text}' already exists."); return
            max_order = max((c.order_index for c in self.animation_file.clips), default=-1)
            self.animation_file.clips.append(AnimationClip(name="New Animation", segment=text, layer="Main", length=1.0, order_index=max_order + 1))
            self.log_message(f"Created segment '{text}'."); self.populate_animation_tree()

    def delete_selected_items(self):
        selected = self.tree.selectedItems();
        if not selected: return
        segs, layers, clips = set(), set(), set()
        for item in selected:
            item_type = item.data(0, 1000)
            if item_type == "segment": segs.add(item.text(0).replace("Segment: ", "").strip())
            elif item_type == "layer": layers.add((item.parent().text(0).replace("Segment: ", "").strip(), item.text(0).replace("  Layer: ", "").strip()))
            elif isinstance(item_type, AnimationClip): clips.add(item_type)
        if not any([segs, layers, clips]): return
        reply = QMessageBox.question(self, 'Confirm Deletion', "Delete selected item(s)?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            initial = len(self.animation_file.clips)
            self.animation_file.clips = [c for c in self.animation_file.clips if c not in clips and c.segment not in segs and (c.segment, c.layer) not in layers]
            self.log_message(f"Deleted {initial - len(self.animation_file.clips)} clip(s)."); self.populate_animation_tree()
            
    def rename_selected_item(self):
        if self.tree.currentItem(): self.tree.editItem(self.tree.currentItem(), 0)

    def on_item_renamed(self, item, col):
        item_type = item.data(0, 1000)
        if item_type == "segment":
            old_name = item.text(0).replace("Segment: ", "").strip()
            if item.childCount() > 0 and item.child(0).childCount() > 0: old_name = item.child(0).child(0).data(0, 1000).segment
            self.handle_segment_rename(item, old_name)
        elif item_type == "layer": self.handle_layer_rename(item)
        elif isinstance(item_type, AnimationClip): self.update_clip_name(item_type, item, item.text(0).replace("    Clip: ", "").strip())

    def handle_segment_rename(self, item, old_name):
        new_name = item.text(0).replace("Segment: ", "").strip(); self.tree.blockSignals(True)
        if not new_name or new_name == old_name: item.setText(0, f"Segment: {old_name}"); self.tree.blockSignals(False); return
        if any(c.segment == new_name for c in self.animation_file.clips):
            QMessageBox.warning(self, "Conflict", f"Segment '{new_name}' exists."); item.setText(0, f"Segment: {old_name}"); self.tree.blockSignals(False); return
        for clip in self.animation_file.clips:
            if clip.segment == old_name: clip.segment = new_name
        self.log_message(f"Renamed segment '{old_name}' to '{new_name}'."); item.setText(0, f"Segment: {new_name}"); self.tree.blockSignals(False); self.populate_animation_tree()

    def handle_layer_rename(self, item):
        new_name, seg_name = item.text(0).replace("Layer: ", "").replace("  ", "").strip(), item.parent().text(0).replace("Segment: ", "").strip()
        old_name = new_name;
        if item.childCount() > 0: old_name = item.child(0).data(0, 1000).layer
        self.tree.blockSignals(True)
        if not new_name or new_name == old_name: item.setText(0, f"  Layer: {old_name}"); self.tree.blockSignals(False); return
        if any(c.layer == new_name and c.segment == seg_name for c in self.animation_file.clips):
            QMessageBox.warning(self, "Conflict", f"Layer '{new_name}' exists in this segment."); item.setText(0, f"  Layer: {old_name}"); self.tree.blockSignals(False); return
        for clip in self.animation_file.clips:
            if clip.segment == seg_name and clip.layer == old_name: clip.layer = new_name
        self.log_message(f"Renamed layer '{old_name}' to '{new_name}' in '{seg_name}'."); item.setText(0, f"  Layer: {new_name}"); self.tree.blockSignals(False); self.populate_animation_tree()

    def update_clip_name(self, clip, item, new_name_raw):
        old, new = clip.name, new_name_raw.strip(); editor = self.properties_panel.name_edit; was_blocked = editor.signalsBlocked(); editor.blockSignals(True)
        if not new or new == old: editor.setText(old); item.setText(0, f"    Clip: {old}"); editor.blockSignals(was_blocked); return
        if any(c is not clip and c.segment==clip.segment and c.layer==clip.layer and c.name==new for c in self.animation_file.clips):
            QMessageBox.warning(self, "Conflict", f"Clip '{new}' exists in this layer."); editor.setText(old); item.setText(0, f"    Clip: {old}"); editor.blockSignals(was_blocked); return
        clip.name = new; self.log_message(f"Renamed clip '{old}' to '{new}'.")
        for other in self.animation_file.clips:
            if other.other_properties.get("NextAnimationName")==old and other.layer==clip.layer and other.segment==clip.segment:
                other.other_properties["NextAnimationName"] = new; self.log_message(f"Updated NextAnimationName for '{other.name}'.")
        editor.setText(new); item.setText(0, f"    Clip: {new}"); editor.blockSignals(was_blocked)

    def duplicate_selected_clip(self):
        item = self.tree.currentItem();
        if not item or not isinstance(item.data(0, 1000), AnimationClip): return
        clip_obj = item.data(0, 1000)
        base, new = clip_obj.name, f"{clip_obj.name} (copy)"; counter = 2
        existing = {c.name for c in self.animation_file.clips if c.segment==clip_obj.segment and c.layer==clip_obj.layer}
        while new in existing: new = f"{base} (copy {counter})"; counter += 1
        new_clip = copy.deepcopy(clip_obj); new_clip.name = new
        new_clip.order_index = max((c.order_index for c in self.animation_file.clips), default=-1) + 1
        self.animation_file.clips.append(new_clip)
        self.log_message(f"Duplicated '{clip_obj.name}' as '{new}'."); self.populate_animation_tree()

    def batch_rename_items(self):
        selected = [item.data(0, 1000) for item in self.tree.selectedItems() if isinstance(item.data(0, 1000), AnimationClip)]
        if not selected: QMessageBox.information(self, "Info", "Select clips to rename."); return
        dialog = BatchRenameDialog(self)
        if dialog.exec():
            find, replace, prefix, suffix = dialog.find_edit.text(), dialog.replace_edit.text(), dialog.prefix_edit.text(), dialog.suffix_edit.text()
            renamed = 0
            for clip in selected:
                original, new = clip.name, clip.name
                if find: new = new.replace(find, replace)
                if prefix: new = prefix + new
                if suffix: new = new + suffix
                if new != original:
                    is_conflict = any(c.name==new and c.layer==clip.layer and c.segment==clip.segment for c in self.animation_file.clips if c is not clip)
                    if is_conflict: self.log_message(f"SKIPPED rename for '{original}' due to conflict."); continue
                    clip.name = new
                    for other in self.animation_file.clips:
                        if other.other_properties.get("NextAnimationName")==original and other.layer==clip.layer and other.segment==clip.segment:
                            other.other_properties["NextAnimationName"] = new
                    renamed += 1
            self.log_message(f"Batch renamed {renamed} clip(s)."); self.populate_animation_tree()

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
                            [KeyframeDecoder.decode_keyframe(kf, 0.0, 3) for kf in controller.properties[axis]],
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
                self.log_message(f"ERROR: Failed to process clip '{clip.name}'. Reason: {e}")
                import traceback; traceback.print_exc()

        if self.current_file_path: self.setWindowTitle(self.windowTitle() + " *")
        self.on_tree_selection_changed(); self.populate_animation_tree()
        return processed_count

    def center_root_on_first_frame(self):
        selected_items = self.tree.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select one or more clips to process."); return

        clips_to_process = [item.data(0, 1000) for item in selected_items if isinstance(item.data(0, 1000), AnimationClip)]
        if not clips_to_process:
            QMessageBox.warning(self, "Invalid Selection", "Please select valid animation clips."); return

        self.log_message(f"Starting 'Center Root (XZ only)' operation for {len(clips_to_process)} clip(s)...")
        clip = clips_to_process[0]
        
        root_options = ['control', 'hipControl', 'pelvisControl']
        root_controller = next((c for name in root_options for c in clip.controllers if c.id == name), None)

        if not root_controller:
            self.log_message(f"ERROR: Clip '{clip.name}' is missing a required root controller. Operation aborted.")
            return

        def get_pos_at_time(controller, axis, time_target=0.0):
            last_v, last_c = 0.0, 3
            for kf_str in controller.properties.get(axis, []):
                t, v, c = KeyframeDecoder.decode_keyframe(kf_str, last_v, last_c)
                if math.isclose(t, time_target, abs_tol=1e-5): return v
                last_v, last_c = v, c
            return 0.0 

        p_root_local = [get_pos_at_time(root_controller, axis, 0.0) for axis in ['X', 'Y', 'Z']]
        
        delta_x = -p_root_local[0]
        delta_y = 0.0
        delta_z = -p_root_local[2]
        delta = (delta_x, delta_y, delta_z)
        
        self.last_center_root_delta_xz = (delta_x, delta_z)
        self.log_message(f"Calculated XZ delta from root '{root_controller.id}' in clip '{clip.name}': ({delta_x:.4f}, {delta_z:.4f}). Applying to all selected clips.")
        
        processed_count = self._apply_position_delta_to_clips(clips_to_process, delta)
        self.log_message(f"Root centering (XZ only) operation finished. Processed {processed_count} clip(s).")

    def move_root_by_offset(self):
        selected_items = self.tree.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, "No Selection", "Please select one or more clips to process."); return
            
        clips_to_process = [item.data(0, 1000) for item in selected_items if isinstance(item.data(0, 1000), AnimationClip)]
        if not clips_to_process:
            QMessageBox.warning(self, "Invalid Selection", "Please select valid animation clips."); return

        dialog = OffsetDialog(self)
        dialog.set_initial_values(self.last_center_root_delta_xz[0], 0.0, self.last_center_root_delta_xz[1])
        
        if dialog.exec():
            offsets = dialog.get_offsets()
            if offsets is None: return
            
            self.log_message(f"Applying manual offset {offsets} to {len(clips_to_process)} clip(s)...")
            processed_count = self._apply_position_delta_to_clips(clips_to_process, offsets)
            self.log_message(f"Manual offset operation finished. Processed {processed_count} clip(s).")


class OffsetDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Move by Offset")
        layout = QFormLayout(self)
        self.x_edit = QLineEdit(); self.y_edit = QLineEdit(); self.z_edit = QLineEdit()
        layout.addRow("X Offset:", self.x_edit); layout.addRow("Y Offset:", self.y_edit); layout.addRow("Z Offset:", self.z_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addRow(buttons)
        
    def set_initial_values(self, x, y, z):
        self.x_edit.setText(f"{x:.4f}"); self.y_edit.setText(f"{y:.4f}"); self.z_edit.setText(f"{z:.4f}")

    def get_offsets(self):
        try:
            return (float(self.x_edit.text()), float(self.y_edit.text()), float(self.z_edit.text()))
        except ValueError:
            QMessageBox.warning(self, "Invalid Input", "Please enter valid numbers for the offsets.")
            return None

class MergeConflictDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Merge Clip Name Conflicts")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("How should clips with conflicting names be handled?"))
        self.rename_radio = QRadioButton("Rename and Add (e.g., 'Clip_merged')"); self.rename_radio.setChecked(True)
        self.replace_radio = QRadioButton("Replace Existing Clips"); self.skip_radio = QRadioButton("Skip Conflicting Clips")
        layout.addWidget(self.rename_radio); layout.addWidget(self.replace_radio); layout.addWidget(self.skip_radio)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def get_selected_strategy(self):
        if self.replace_radio.isChecked(): return "replace"
        if self.skip_radio.isChecked(): return "skip"
        return "rename"

class BatchRenameDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Batch Rename Clips")
        layout = QFormLayout(self)
        self.find_edit, self.replace_edit, self.prefix_edit, self.suffix_edit = QLineEdit(), QLineEdit(), QLineEdit(), QLineEdit()
        layout.addRow("Find text:", self.find_edit); layout.addRow("Replace with:", self.replace_edit)
        layout.addRow(QLabel("--- OR ---"))
        layout.addRow("Add Prefix:", self.prefix_edit); layout.addRow("Add Suffix:", self.suffix_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addRow(buttons)

# --- 5. Application Entry Point ---
if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())