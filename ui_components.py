# ui_components.py
import copy

from PyQt6.QtCore import Qt, QMimeData
from PyQt6.QtGui import QIcon, QDrag
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QTreeWidget, QAbstractItemView, QLabel, QMenu,
    QMessageBox, QLineEdit, QListWidget, QFormLayout, QDialog, QDialogButtonBox,
    QRadioButton
)

from data_models import AnimationClip, FloatParameter, ControllerTarget, TriggerGroup
from keyframe_logic import KeyframeEncoder

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
        if not items:
            return
        item = items[0]
        drag = QDrag(self)
        mime_data = QMimeData()
        
        data = item.data(0, 1000)
        if isinstance(data, tuple) and data[0] == 'layer':
            if len(items) > 1: return
            mime_data.setText("layer-drag")
            drag.setMimeData(mime_data)
            drag.exec(Qt.DropAction.MoveAction)
        elif isinstance(data, AnimationClip):
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
            
    def handle_layer_merge(self, event):
        source_item = self.selectedItems()[0]
        target_item_at_point = self.itemAt(event.position().toPoint())
        
        src_data = source_item.data(0, 1000)
        tgt_data = target_item_at_point.data(0, 1000) if target_item_at_point else None
        
        if not (src_data and isinstance(src_data, tuple) and src_data[0] == 'layer'):
            event.ignore()
            return

        target_layer_item = None
        if tgt_data:
            if isinstance(tgt_data, tuple) and tgt_data[0] == 'layer':
                target_layer_item = target_item_at_point
            elif isinstance(tgt_data, AnimationClip):
                target_layer_item = target_item_at_point.parent()
        
        if not target_layer_item or source_item == target_layer_item:
            event.ignore()
            return
            
        src_layer_name = src_data[3]
        tgt_layer_name = target_layer_item.data(0, 1000)[3]
        
        reply = QMessageBox.question(self, 'Confirm Layer Merge', 
                                     f"Are you sure you want to merge layer '{src_layer_name}' into '{tgt_layer_name}'?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, 
                                     QMessageBox.StandardButton.No)
        
        if reply == QMessageBox.StandardButton.Yes:
            self.parent_window.app_logic.merge_layers(src_data, target_layer_item.data(0, 1000))
            event.acceptProposedAction()
        else:
            event.ignore()

    def handle_clip_drop(self, event):
        source_items = self.selectedItems()
        target_item = self.itemAt(event.position().toPoint())
        if not source_items or not target_item:
            event.ignore()
            return
            
        is_copy = event.proposedAction() == Qt.DropAction.CopyAction
        source_layer_item = source_items[0].parent()
        
        target_layer_item = None
        target_clip_for_pos = None
        
        target_data = target_item.data(0, 1000)
        if isinstance(target_data, AnimationClip):
            target_layer_item = target_item.parent()
            target_clip_for_pos = target_item
        elif isinstance(target_data, tuple) and target_data[0] == 'layer':
            target_layer_item = target_item
        
        if not target_layer_item:
            event.ignore()
            return
            
        app_logic = self.parent_window.app_logic
        dragged_clips_ids = {id(item.data(0, 1000)) for item in source_items}
        
        if not is_copy and source_layer_item == target_layer_item:
            drop_pos_enum = self.dropIndicatorPosition()
            drop_pos = 'Below' if drop_pos_enum == QAbstractItemView.DropIndicatorPosition.BelowItem else 'Above'
            target_clip_id = id(target_clip_for_pos.data(0, 1000)) if target_clip_for_pos else None
            app_logic.reorder_clips_in_layer(target_layer_item.data(0, 1000), dragged_clips_ids, target_clip_id, drop_pos)
        else:
            app_logic.move_or_copy_clips_to_layer(dragged_clips_ids, target_layer_item.data(0, 1000), is_copy)
        
        event.acceptProposedAction()
        
    def open_context_menu(self, position):
        menu = QMenu(self)
        selected = self.selectedItems()
        if selected:
            if len(selected) == 1:
                item = selected[0]
                rename_action = menu.addAction("Rename...")
                rename_action.setShortcut("F2")
                rename_action.triggered.connect(self.parent_window.rename_selected_item)
                if isinstance(item.data(0, 1000), AnimationClip):
                    duplicate_action = menu.addAction("Duplicate Clip")
                    duplicate_action.setShortcut("Ctrl+D")
                    duplicate_action.triggered.connect(self.parent_window.duplicate_selected_clip)
            delete_action = menu.addAction(QIcon.fromTheme("edit-delete"), f"Delete {len(selected)} item(s)")
            delete_action.setShortcut("Delete")
            delete_action.triggered.connect(self.parent_window.delete_selected_items)
        if not menu.isEmpty():
            menu.exec(self.viewport().mapToGlobal(position))

class ClipPropertiesPanel(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.clip = None
        self.current_tree_item = None
        self.atom_label_widget = None
        self.atom_field_widget = None
        self.init_ui()
        self.clear()
    
    def init_ui(self):
        self.layout = QVBoxLayout(self)
        self.form_layout = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.editingFinished.connect(self.on_name_changed)
        self.form_layout.addRow("Name:", self.name_edit)
        self.layout.addLayout(self.form_layout)
        
        self.layout.addWidget(QLabel("<b>General</b>"))
        self.general_form_layout = QFormLayout()
        
        self.atom_label = QLabel()
        self.segment_label = QLabel()
        self.layer_label = QLabel()
        self.length_label = QLabel()
        self.loop_label = QLabel()
        
        self.atom_label_widget = QLabel("Atom:")
        self.atom_field_widget = self.atom_label
        self.general_form_layout.addRow(self.atom_label_widget, self.atom_field_widget)
        
        self.general_form_layout.addRow("Segment:", self.segment_label)
        self.general_form_layout.addRow("Layer:", self.layer_label)
        self.general_form_layout.addRow("Length:", self.length_label)
        self.general_form_layout.addRow("Loop:", self.loop_label)
        self.layout.addLayout(self.general_form_layout)
        
        self.layout.addWidget(QLabel("<b>Sequencing</b>"))
        self.sequence_form_layout = QFormLayout()
        self.next_anim_label = QLabel()
        self.sequence_form_layout.addRow("Next Animation:", self.next_anim_label)
        self.layout.addLayout(self.sequence_form_layout)
        
        self.layout.addWidget(QLabel("<b>Targets</b>"))
        self.targets_list = QListWidget()
        self.layout.addWidget(self.targets_list)
        self.layout.addStretch()

    def display_clip_properties(self, clip, item):
        self.clip, self.current_tree_item = clip, item
        self.name_edit.blockSignals(True)
        self.name_edit.setText(clip.name)
        self.name_edit.blockSignals(False)
        
        self.atom_label.setText(clip.atom_id or "N/A")
        self.segment_label.setText(clip.segment)
        self.layer_label.setText(clip.layer)
        self.length_label.setText(f"{clip.length:.3f}s")
        self.loop_label.setText("Yes" if clip.other_properties.get('Loop', '0') == '1' else "No")
        self.next_anim_label.setText(clip.other_properties.get('NextAnimationName', 'None'))
        self.targets_list.clear()
        
        animation_file = self.main_window.app_logic.animation_file
        is_scene = animation_file.is_scene if animation_file else False
        
        self.atom_label_widget.setVisible(is_scene)
        self.atom_field_widget.setVisible(is_scene)
        
        targets = (
            [f"[C] {c.id}" for c in clip.controllers] + 
            [f"[F] {p.storable}/{p.name}" for p in clip.float_params] +
            [f"[T] {tg.name}" for tg in clip.trigger_groups]
        )
        if targets:
            self.targets_list.addItems(sorted(targets))
        else:
            self.targets_list.addItem("No targets in this clip.")
        self.show()

    def on_name_changed(self):
        if self.clip and self.name_edit.text() != self.clip.name:
            self.main_window.app_logic.rename_item(self.clip, self.name_edit.text())
            
    def clear(self):
        self.clip, self.current_tree_item = None, None
        self.name_edit.blockSignals(True)
        self.name_edit.clear()
        self.name_edit.blockSignals(False)
        self.hide()

class OffsetDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Move by Offset")
        layout = QFormLayout(self)
        self.x_edit = QLineEdit()
        self.y_edit = QLineEdit()
        self.z_edit = QLineEdit()
        layout.addRow("X Offset:", self.x_edit)
        layout.addRow("Y Offset:", self.y_edit)
        layout.addRow("Z Offset:", self.z_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)
        
    def set_initial_values(self, x, y, z):
        self.x_edit.setText(f"{x:.4f}")
        self.y_edit.setText(f"{y:.4f}")
        self.z_edit.setText(f"{z:.4f}")

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
        self.rename_radio = QRadioButton("Rename and Add (e.g., 'Clip_merged')")
        self.rename_radio.setChecked(True)
        self.replace_radio = QRadioButton("Replace Existing Clips")
        self.skip_radio = QRadioButton("Skip Conflicting Clips")
        layout.addWidget(self.rename_radio)
        layout.addWidget(self.replace_radio)
        layout.addWidget(self.skip_radio)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_selected_strategy(self):
        if self.replace_radio.isChecked(): return "replace"
        if self.skip_radio.isChecked(): return "skip"
        return "rename"

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
        
    def get_params(self):
        return {
            "find": self.find_edit.text(),
            "replace": self.replace_edit.text(),
            "prefix": self.prefix_edit.text(),
            "suffix": self.suffix_edit.text()
        }