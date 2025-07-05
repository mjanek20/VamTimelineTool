import sys
import json
import copy
from collections import defaultdict

# Using PyQt6 for the GUI
from PyQt6.QtCore import Qt, QMimeData
from PyQt6.QtGui import QAction, QIcon, QDrag
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTreeWidget, QTreeWidgetItem, QAbstractItemView, QFileDialog, QLabel,
    QMenu, QMessageBox, QInputDialog, QToolBar
)

# --- 1. Data Model ---

class FloatParameter:
    """Represents a single 'FloatParams' entry within a clip."""
    def __init__(self, storable, name, value, min_val, max_val):
        self.storable = storable
        self.name = name
        self.value = value
        self.min = min_val
        self.max = max_val

    @classmethod
    def from_dict(cls, data):
        return cls(
            data.get("Storable"),
            data.get("Name"),
            data.get("Value", []),
            data.get("Min"),
            data.get("Max")
        )

    def to_dict(self):
        return {
            "Storable": self.storable,
            "Name": self.name,
            "Value": self.value,
            "Min": self.min,
            "Max": self.max
        }


class AnimationClip:
    """Represents a single animation clip from the 'Clips' list."""
    def __init__(self, name, segment, layer, length, order_index=0, **kwargs):
        self.name = name
        self.segment = segment
        self.layer = layer
        self.length = length
        self.order_index = order_index
        self.other_properties = kwargs
        self.float_params = []

    @classmethod
    def from_dict(cls, data):
        known_keys = {"AnimationName", "AnimationSegment", "AnimationLayer", "AnimationLength", "FloatParams", "OrderIndex"}
        clip_name = data.get("AnimationName", "Unnamed")
        segment = data.get("AnimationSegment", "Default")
        layer = data.get("AnimationLayer", "Default")
        length = float(data.get("AnimationLength", 0.0))
        order_index = int(data.get("OrderIndex", 0))

        other_props = {k: v for k, v in data.items() if k not in known_keys}
        
        instance = cls(clip_name, segment, layer, length, order_index, **other_props)
        
        if "FloatParams" in data:
            instance.float_params = [FloatParameter.from_dict(p) for p in data["FloatParams"]]
            
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
            sorted_params = sorted(self.float_params, key=lambda p: p.name)
            data["FloatParams"] = [p.to_dict() for p in sorted_params]
        return data


class AnimationFile:
    """Represents the entire animation file."""
    def __init__(self, version, atom_type):
        self.version = version
        self.atom_type = atom_type
        self.clips = []

    @classmethod
    def from_dict(cls, data):
        instance = cls(data.get("SerializeVersion"), data.get("AtomType"))
        if "Clips" in data:
            for i, clip_data in enumerate(data["Clips"]):
                clip_data['OrderIndex'] = i
            instance.clips = [AnimationClip.from_dict(c) for c in data["Clips"]]
        
        return instance

    def to_dict(self):
        # Sort clips before saving to maintain the order from the UI
        self.clips.sort(key=lambda c: c.order_index)
        return {
            "SerializeVersion": self.version,
            "AtomType": self.atom_type,
            "Clips": [c.to_dict() for c in self.clips]
        }


# --- 2. Custom UI Components ---

class AnimationTreeWidget(QTreeWidget):
    """Custom QTreeWidget to handle drag & drop and context menus."""
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

    # --- FIX START ---
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
    # --- FIX END ---
            
    def startDrag(self, supportedActions):
        items = self.selectedItems()
        if not items: return
        
        item = items[0]
        drag = QDrag(self)
        mime_data = QMimeData()
        
        if item.parent() and not item.parent().parent():
            if len(items) > 1: return
            mime_data.setText("layer-drag")
            drag.setMimeData(mime_data)
            drag.exec(Qt.DropAction.MoveAction)
        elif item.parent() and item.parent().parent():
            mime_data.setText("clip-drag")
            drag.setMimeData(mime_data)
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

    def handle_layer_merge(self, event):
        source_layer_item = self.selectedItems()[0]
        target_item_at_point = self.itemAt(event.position().toPoint())
        
        target_layer_item = None
        if target_item_at_point:
            if target_item_at_point.parent() and not target_item_at_point.parent().parent():
                target_layer_item = target_item_at_point
            elif target_item_at_point.parent() and target_item_at_point.parent().parent():
                target_layer_item = target_item_at_point.parent()
        
        if not target_layer_item or source_layer_item == target_layer_item:
            event.ignore()
            return
            
        if source_layer_item.parent() != target_layer_item.parent():
            QMessageBox.warning(self, "Invalid Operation", "Layers can only be merged within the same segment.")
            return

        source_name = source_layer_item.text(0).strip()
        target_name = target_layer_item.text(0).strip()
        reply = QMessageBox.question(self, 'Confirm Layer Merge',
                                     f"Are you sure you want to merge '{source_name}' into '{target_name}'?\n\n"
                                     "This will add missing controllers to all animations in the target layer and cannot be undone.",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        
        if reply == QMessageBox.StandardButton.No:
            event.ignore()
            return

        segment_name = target_layer_item.parent().text(0).replace("Segment: ", "").strip()
        source_layer_name = source_layer_item.text(0).replace("  Layer: ", "").strip()
        target_layer_name = target_layer_item.text(0).replace("  Layer: ", "").strip()

        source_clips = self.get_layer_clips(segment_name, source_layer_name)
        target_clips = self.get_layer_clips(segment_name, target_layer_name)

        all_targets_map = {}
        for clip in source_clips + target_clips:
            for param in clip.float_params:
                param_key = (param.storable, param.name)
                if param_key not in all_targets_map:
                    all_targets_map[param_key] = param

        target_clips_by_name = {clip.name: clip for clip in target_clips}

        for source_clip in source_clips:
            if source_clip.name in target_clips_by_name:
                target_clip = target_clips_by_name[source_clip.name]
                existing_target_keys = {(p.storable, p.name) for p in target_clip.float_params}
                for param in source_clip.float_params:
                    if (param.storable, param.name) not in existing_target_keys:
                        target_clip.float_params.append(param)
                self.parent_window.animation_file.clips.remove(source_clip)
            else:
                source_clip.layer = target_layer_name
        
        final_target_clips = self.get_layer_clips(segment_name, target_layer_name)
        
        for clip in final_target_clips:
            clip_target_keys = {(p.storable, p.name) for p in clip.float_params}
            
            for target_key, template_param in all_targets_map.items():
                if target_key not in clip_target_keys:
                    default_keyframes = [{"t": "0.0", "v": "0.0"}, {"t": str(clip.length), "v": "0.0"}]
                    new_param = FloatParameter(
                        storable=template_param.storable, name=template_param.name,
                        value=default_keyframes, min_val=template_param.min, max_val=template_param.max
                    )
                    clip.float_params.append(new_param)

        self.parent_window.populate_animation_tree()
        event.acceptProposedAction()

    def handle_clip_drop(self, event):
        source_items = self.selectedItems()
        target_item = self.itemAt(event.position().toPoint())
        if not source_items or not target_item:
            event.ignore()
            return

        is_copy = event.proposedAction() == Qt.DropAction.CopyAction
        source_layer_item = source_items[0].parent()

        target_layer_item = None
        if isinstance(target_item.data(0, 1000), AnimationClip):
            target_layer_item = target_item.parent()
        elif target_item.childCount() > 0 and isinstance(target_item.child(0).data(0, 1000), AnimationClip):
            target_layer_item = target_item
        
        if not target_layer_item:
            event.ignore()
            return
        
        if not is_copy and source_layer_item == target_layer_item:
            segment_name = source_layer_item.parent().text(0).replace("Segment: ", "").strip()
            layer_name = source_layer_item.text(0).replace("  Layer: ", "").strip()
            
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
            else:
                target_index = len(remaining_clips)
                
            for clip_obj in reversed(dragged_clip_objs):
                remaining_clips.insert(target_index, clip_obj)

            for i, clip_obj in enumerate(remaining_clips):
                clip_obj.order_index = i
        else:
            self.handle_clip_move_or_copy(event, is_copy, target_layer_item)
            
        self.parent_window.populate_animation_tree()
        event.acceptProposedAction()

    def handle_clip_move_or_copy(self, event, is_copy, target_layer_item):
        source_items = self.selectedItems()
        target_layer_name = target_layer_item.text(0).replace("  Layer: ", "").strip()
        target_segment_name = target_layer_item.parent().text(0).replace("Segment: ", "").strip()
        
        clips_in_target_layer = self.get_layer_clips(target_segment_name, target_layer_name)
        existing_names_in_target = {c.name for c in clips_in_target_layer}
        
        max_order_index = -1
        if clips_in_target_layer:
            max_order_index = max(c.order_index for c in clips_in_target_layer)

        for source_item in source_items:
            source_clip_obj = source_item.data(0, 1000)
            new_name = source_clip_obj.name
            
            if new_name in existing_names_in_target:
                msg_box = QMessageBox(self)
                msg_box.setWindowTitle("Name Conflict")
                msg_box.setText(f"A clip named '{new_name}' already exists in the target layer.")
                replace_button = msg_box.addButton("Replace", QMessageBox.ButtonRole.YesRole)
                keep_both_button = msg_box.addButton("Keep Both", QMessageBox.ButtonRole.YesRole)
                skip_button = msg_box.addButton("Skip", QMessageBox.ButtonRole.NoRole)
                cancel_all_button = msg_box.addButton("Cancel All", QMessageBox.ButtonRole.RejectRole)
                msg_box.exec()
                clicked_btn = msg_box.clickedButton()

                if clicked_btn == replace_button:
                    clip_to_remove = next((c for c in clips_in_target_layer if c.name == new_name), None)
                    if clip_to_remove: self.parent_window.animation_file.clips.remove(clip_to_remove)
                elif clicked_btn == keep_both_button:
                    counter = 2
                    while True:
                        candidate_name = f"{new_name}_{counter}"
                        if candidate_name not in existing_names_in_target:
                            new_name = candidate_name
                            break
                        counter += 1
                elif clicked_btn == skip_button: continue
                elif clicked_btn == cancel_all_button: event.ignore(); return

            max_order_index += 1
            if is_copy:
                new_clip_obj = copy.deepcopy(source_clip_obj)
                new_clip_obj.name = new_name
                new_clip_obj.segment = target_segment_name
                new_clip_obj.layer = target_layer_name
                new_clip_obj.order_index = max_order_index
                self.parent_window.animation_file.clips.append(new_clip_obj)
                existing_names_in_target.add(new_name)
            else:
                source_clip_obj.name = new_name
                source_clip_obj.segment = target_segment_name
                source_clip_obj.layer = target_layer_name
                source_clip_obj.order_index = max_order_index

    def open_context_menu(self, position):
        menu = QMenu()
        new_segment_action = menu.addAction("New Segment...")
        menu.addSeparator()
        selected_items = self.selectedItems()
        if selected_items:
            if any(item.parent() and item.parent().parent() for item in selected_items):
                delete_action = menu.addAction(QIcon.fromTheme("edit-delete"), f"Delete {len(selected_items)} selected item(s)")
                delete_action.triggered.connect(self.parent_window.delete_selected_items)
        action = menu.exec(self.viewport().mapToGlobal(position))
        if action == new_segment_action: self.parent_window.create_new_segment()

# --- 4. Main Application Window ---

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.animation_file = None
        self.setWindowTitle("VamTimeline Animation Editor")
        self.setGeometry(100, 100, 1200, 800)
        self.init_ui()

    def init_ui(self):
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("&File")

        open_action = QAction(QIcon.fromTheme("document-open"), "&Open...", self)
        open_action.triggered.connect(self.open_file)
        file_menu.addAction(open_action)

        save_as_action = QAction(QIcon.fromTheme("document-save-as"), "&Save As...", self)
        save_as_action.triggered.connect(self.save_file_as)
        file_menu.addAction(save_as_action)

        file_menu.addSeparator()
        
        edit_menu = menu_bar.addMenu("&Edit")
        delete_action = QAction(QIcon.fromTheme("edit-delete"), "&Delete Selected", self)
        delete_action.setShortcut("Delete")
        delete_action.triggered.connect(self.delete_selected_items)
        edit_menu.addAction(delete_action)

        file_menu.addSeparator()

        exit_action = QAction("E&xit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        toolbar = self.addToolBar("Main Toolbar")
        toolbar.addAction(open_action)
        toolbar.addAction(save_as_action)
        toolbar.addSeparator()
        toolbar.addAction(delete_action)
        
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)
        
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_panel.setFixedWidth(400)
        
        self.tree = AnimationTreeWidget(self)
        self.tree.setHeaderLabels(["Segment / Layer / Animation"])
        
        left_layout.addWidget(QLabel("Animation Structure:"))
        left_layout.addWidget(self.tree)
        
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        
        self.properties_label = QLabel("Select an item in the tree to see its properties.")
        right_layout.addWidget(self.properties_label)
        right_layout.addStretch()

        main_layout.addWidget(left_panel)
        main_layout.addWidget(right_panel)

    def open_file(self):
        file_name, _ = QFileDialog.getOpenFileName(self, "Open Animation File", "", "JSON Files (*.json)")
        if file_name:
            try:
                with open(file_name, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.animation_file = AnimationFile.from_dict(data)
                self.populate_animation_tree()
                self.setWindowTitle(f"VamTimeline Animation Editor - {file_name}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Error loading file: {e}")

    def save_file_as(self):
        if not self.animation_file:
            return
            
        file_name, _ = QFileDialog.getSaveFileName(self, "Save Animation File As", "", "JSON Files (*.json)")
        if file_name:
            if not file_name.lower().endswith('.json'):
                file_name += '.json'
            try:
                data_to_save = self.animation_file.to_dict()
                with open(file_name, 'w', encoding='utf-8') as f:
                    json.dump(data_to_save, f, indent=3, ensure_ascii=False)
                self.setWindowTitle(f"VamTimeline Animation Editor - {file_name}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Error saving file: {e}")

    def populate_animation_tree(self):
        self.tree.clear()
        if not self.animation_file:
            return

        grouped_clips = defaultdict(lambda: defaultdict(list))
        # Sortowanie klipów po order_index zapewnia, że przetwarzamy je w prawidłowej kolejności
        for clip in sorted(self.animation_file.clips, key=lambda c: c.order_index):
            grouped_clips[clip.segment][clip.layer].append(clip)

        for segment_name in sorted(grouped_clips.keys()):
            layers = grouped_clips[segment_name]
            segment_item = QTreeWidgetItem(self.tree, [f"Segment: {segment_name}"])
            
            for layer_name in sorted(layers.keys()):
                clips = layers[layer_name]
                layer_item = QTreeWidgetItem(segment_item, [f"  Layer: {layer_name}"])
                
                for clip_obj in clips: # clips already sorted by order_index
                    clip_item = QTreeWidgetItem(layer_item, [f"    Clip: {clip_obj.name}"])
                    clip_item.setData(0, 1000, clip_obj)
        
        self.tree.expandAll()
        
    def create_new_segment(self):
        if not self.animation_file:
            QMessageBox.warning(self, "Warning", "Please open an animation file first.")
            return

        text, ok = QInputDialog.getText(self, 'New Segment', 'Enter a name for the new segment:')

        if ok and text:
            max_order = max(c.order_index for c in self.animation_file.clips) if self.animation_file.clips else -1
            new_clip = AnimationClip(name="New Animation", segment=text, layer="Main", length=1.0, order_index=max_order + 1)
            self.animation_file.clips.append(new_clip)
            self.populate_animation_tree()

    def delete_selected_items(self):
        selected_items = self.tree.selectedItems()
        if not selected_items:
            return

        clips_to_delete = []
        for item in selected_items:
            if item.parent() and item.parent().parent():
                clip_obj = item.data(0, 1000)
                if isinstance(clip_obj, AnimationClip):
                    clips_to_delete.append(clip_obj)
        
        if not clips_to_delete:
            return

        reply = QMessageBox.question(self, 'Confirm Deletion',
                                     f"Are you sure you want to delete {len(clips_to_delete)} clip(s)?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)

        if reply == QMessageBox.StandardButton.Yes:
            for clip_obj in clips_to_delete:
                if clip_obj in self.animation_file.clips:
                    self.animation_file.clips.remove(clip_obj)
            
            self.populate_animation_tree()


# --- 5. Application Entry Point ---
if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())