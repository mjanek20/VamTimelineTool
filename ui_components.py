# ui_components.py
import copy

from PyQt6.QtCore import Qt, QMimeData
from PyQt6.QtGui import QIcon, QDrag, QColor, QBrush
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QTreeWidget, QAbstractItemView, QLabel, QMenu,
    QMessageBox, QLineEdit, QListWidget, QListWidgetItem, QFormLayout, QDialog, QDialogButtonBox,
    QRadioButton, QToolTip, QApplication, QGroupBox
)

from data_models import AnimationClip, FloatParameter, ControllerTarget, TriggerGroup
from enums import DropActionType

class DeleteTargetScopeDialog(QDialog):
    """A dialog to ask the user the scope of the target deletion."""
    def __init__(self, layer_name, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Define Deletion Scope")
        layout = QVBoxLayout(self)

        info_label = QLabel(
            "Deleting targets will change this clip's structure, making it "
            "incompatible with its current layer.\n\nHow do you want to proceed?"
        )
        layout.addWidget(info_label)

        self.move_radio = QRadioButton("Delete from this clip only and move it to a new/compatible layer.")
        self.move_radio.setChecked(True)
        layout.addWidget(self.move_radio)

        self.layer_radio = QRadioButton(f"Delete from ALL clips in the '{layer_name}' layer.")
        layout.addWidget(self.layer_radio)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_selected_scope(self):
        if self.layer_radio.isChecked():
            return "layer"
        return "move"

class AnimationTreeWidget(QTreeWidget):
    def __init__(self, parent_window):
        super().__init__()
        self.parent_window = parent_window
        # Drag & Drop setup
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        # Context Menu
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.open_context_menu)
        # Double Click
        self.itemDoubleClicked.connect(self.on_item_double_clicked)
        
        # Custom widget for persistent drag-and-drop tooltips
        self.drag_indicator_label = QLabel(self, Qt.WindowType.ToolTip)
        self.drag_indicator_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.drag_indicator_label.setStyleSheet("""
            QLabel {
                background-color: #333;
                color: white;
                border: 1px solid #555;
                padding: 4px;
                border-radius: 3px;
            }
        """)
        self.drag_indicator_label.hide()
        
        self.dragged_items_data = None
        self.last_highlighted_item = None

    def _clear_drop_indicator(self):
        """Resets the background color of the last highlighted item."""
        if self.last_highlighted_item:
            try:
                if self.last_highlighted_item.treeWidget() is not None:
                    self.last_highlighted_item.setBackground(0, QBrush())
            except RuntimeError:
                pass
        self.last_highlighted_item = None

    def startDrag(self, supportedActions):
        items = self.selectedItems()
        if not items: return
        
        self.dragged_items_data = [item.data(0, 1000) for item in items]
        
        data = self.dragged_items_data[0]
        mime_data = QMimeData()
        if isinstance(data, AnimationClip):
            mime_data.setText("clip-drag")
        elif isinstance(data, tuple) and data[0] == 'layer':
            if len(items) > 1: return
            mime_data.setText("layer-drag")
        else:
            self.dragged_items_data = None
            return

        drag = QDrag(self)
        drag.setMimeData(mime_data)
        
        drag.exec(supportedActions)
        
        # Final cleanup after the operation is fully complete
        self.drag_indicator_label.hide()
        self._clear_drop_indicator()
        self.dragged_items_data = None
        
    def dragEnterEvent(self, event):
        event.accept()

    def dragMoveEvent(self, event):
        target_item = self.itemAt(event.position().toPoint())
        
        # Clear previous highlight if the target changed
        if self.last_highlighted_item and self.last_highlighted_item != target_item:
            self._clear_drop_indicator()

        if not target_item or not self.dragged_items_data:
            self.drag_indicator_label.hide()
            event.ignore()
            return
        
        is_child = False
        parent = target_item
        while parent:
            if parent.data(0, 1000) in self.dragged_items_data:
                is_child = True
                break
            parent = parent.parent()
        
        if target_item.data(0, 1000) in self.dragged_items_data or is_child:
            self.drag_indicator_label.hide()
            self._clear_drop_indicator()
            event.ignore()
            return

        target_data = target_item.data(0, 1000)
        modifiers = QApplication.keyboardModifiers()
        is_copy = (modifiers & Qt.KeyboardModifier.ControlModifier) == Qt.KeyboardModifier.ControlModifier
        action_type, details = self.parent_window.app_logic.predict_drop_action(self.dragged_items_data, target_data, is_copy)
        
        self.last_highlighted_item = target_item
        
        tooltip_text = ""
        if action_type in [DropActionType.REORDER_CLIPS, DropActionType.MOVE_CLIPS_COMPATIBLE, DropActionType.COPY_CLIPS_COMPATIBLE]:
            target_item.setBackground(0, QColor("#2a4"))
            tooltip_text = f"<b>OK:</b> {details}"
            event.acceptProposedAction()
        elif action_type in [DropActionType.MOVE_CLIPS_NEW_LAYER, DropActionType.COPY_CLIPS_NEW_LAYER]:
            target_item.setBackground(0, QColor("#27a"))
            tooltip_text = f"<b>Info:</b> {details}"
            event.acceptProposedAction()
        elif action_type == DropActionType.MERGE_LAYERS:
            target_item.setBackground(0, QColor("#c82"))
            tooltip_text = f"<b>Warning:</b> {details}"
            event.acceptProposedAction()
        else:
            target_item.setBackground(0, QColor("#800"))
            tooltip_text = f"<b>Invalid:</b> {details}"
            event.ignore()
        
        self.drag_indicator_label.setText(tooltip_text)
        
        # --- FIX: Use mapToGlobal on the widget's viewport ---
        pos = self.viewport().mapToGlobal(event.position().toPoint())
        self.drag_indicator_label.move(pos.x() + 15, pos.y() + 15)
        self.drag_indicator_label.show()
        self.drag_indicator_label.adjustSize()

            
    def dragLeaveEvent(self, event):
        self._clear_drop_indicator()
        self.drag_indicator_label.hide()
        event.accept()

    def dropEvent(self, event):
        self._clear_drop_indicator()
        self.drag_indicator_label.hide()

        if not self.dragged_items_data:
            event.ignore()
            return
            
        mime_text = event.mimeData().text()
        
        if mime_text == "clip-drag":
            self.handle_clip_drop(event)
        elif mime_text == "layer-drag":
            self.handle_layer_merge(event)
        else:
            event.ignore()

    def on_item_double_clicked(self, item, column):
        self.parent_window.rename_selected_item()
        
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_F2:
            self.parent_window.rename_selected_item()
        elif event.key() == Qt.Key.Key_Delete:
            self.parent_window.delete_selected_items()
        else:
            super().keyPressEvent(event)
            
    def handle_layer_merge(self, event):
        source_data = self.dragged_items_data[0]
        target_item_at_point = self.itemAt(event.position().toPoint())
        
        target_layer_item = self.get_target_layer_item(target_item_at_point)
        
        if not target_layer_item or source_data == target_layer_item.data(0, 1000):
            event.ignore()
            return
            
        src_layer_name = source_data[3]
        tgt_layer_name = target_layer_item.data(0, 1000)[3]
        
        reply = QMessageBox.question(self, 'Confirm Layer Merge', 
                                     f"Are you sure you want to merge layer '{src_layer_name}' into '{tgt_layer_name}'?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, 
                                     QMessageBox.StandardButton.No)
        
        if reply == QMessageBox.StandardButton.Yes:
            self.parent_window.app_logic.merge_layers(source_data, target_layer_item.data(0, 1000))
            event.acceptProposedAction()
        else:
            event.ignore()

    def get_target_layer_item(self, target_item):
        """Helper to find the layer item from a drop target."""
        if not target_item:
            return None
        
        target_data = target_item.data(0, 1000)
        if isinstance(target_data, AnimationClip):
            return target_item.parent()
        elif isinstance(target_data, tuple) and target_data[0] == 'layer':
            return target_item
        return None

    def handle_clip_drop(self, event):
        target_item = self.itemAt(event.position().toPoint())
        if not target_item:
            event.ignore(); return
            
        is_copy = (QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier) == Qt.KeyboardModifier.ControlModifier
        
        source_clip_sample = self.dragged_items_data[0]
        source_layer_data = ('layer', source_clip_sample.atom_id, source_clip_sample.segment, source_clip_sample.layer)
        
        target_layer_item = self.get_target_layer_item(target_item)
        
        if not target_layer_item:
            event.ignore(); return
            
        app_logic = self.parent_window.app_logic
        dragged_clips_ids = {id(item) for item in self.dragged_items_data}
        
        if not is_copy and source_layer_data == target_layer_item.data(0, 1000): # Reorder
            drop_pos_enum = self.dropIndicatorPosition()
            drop_pos = 'Below' if drop_pos_enum == QAbstractItemView.DropIndicatorPosition.BelowItem else 'Above'
            target_clip_for_pos = target_item if isinstance(target_item.data(0, 1000), AnimationClip) else None
            target_clip_id = id(target_clip_for_pos.data(0, 1000)) if target_clip_for_pos else None
            app_logic.reorder_clips_in_layer(target_layer_item.data(0, 1000), dragged_clips_ids, target_clip_id, drop_pos)
        else: # Move or Copy
            app_logic.move_or_copy_clips_to_layer(dragged_clips_ids, target_layer_item.data(0, 1000), is_copy)
        
        event.acceptProposedAction()
        
    def open_context_menu(self, position):
        menu = QMenu(self)
        selected = self.selectedItems()
        if selected:
            if len(selected) == 1:
                item = selected[0]
                data = item.data(0, 1000)
                
                if isinstance(data, tuple) and data[0] == 'segment':
                    rename_action = menu.addAction("Rename...")
                    rename_action.setShortcut("F2")
                    rename_action.triggered.connect(self.parent_window.rename_selected_item)
                    
                    duplicate_action = menu.addAction("Duplicate Segment")
                    duplicate_action.triggered.connect(self.parent_window.duplicate_selected_segment)

                elif isinstance(data, tuple) and data[0] == 'layer':
                    rename_action = menu.addAction("Rename...")
                    rename_action.setShortcut("F2")
                    rename_action.triggered.connect(self.parent_window.rename_selected_item)

                elif isinstance(data, AnimationClip):
                    rename_action = menu.addAction("Rename...")
                    rename_action.setShortcut("F2")
                    rename_action.triggered.connect(self.parent_window.rename_selected_item)
                    duplicate_action = menu.addAction("Duplicate Clip")
                    duplicate_action.setShortcut("Ctrl+D")
                    duplicate_action.triggered.connect(self.parent_window.duplicate_selected_clip)

            delete_action = menu.addAction(f"Delete {len(selected)} item(s)")
            delete_action.setShortcut("Delete")
            delete_action.triggered.connect(self.parent_window.delete_selected_items)

        if not menu.isEmpty():
            menu.exec(self.viewport().mapToGlobal(position))

class TargetListWidget(QListWidget):
    """A QListWidget customized for handling target deletion."""
    def __init__(self, parent_panel):
        super().__init__()
        self.parent_panel = parent_panel
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.open_context_menu)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Delete:
            self.parent_panel.handle_delete_targets()
        else:
            super().keyPressEvent(event)

    def open_context_menu(self, position):
        selected_items = self.selectedItems()
        if not selected_items:
            return

        menu = QMenu(self)
        delete_action = menu.addAction(f"Delete {len(selected_items)} Target(s)")
        delete_action.triggered.connect(self.parent_panel.handle_delete_targets)
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
        self.targets_list = TargetListWidget(self)
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
        
        all_targets = []
        for c in sorted(clip.controllers, key=lambda x: x.id):
            all_targets.append(("[C]", c.id, c))
        for p in sorted(clip.float_params, key=lambda x: (x.storable, x.name)):
            all_targets.append(("[F]", f"{p.storable}/{p.name}", p))
        for tg in sorted(clip.trigger_groups, key=lambda x: x.name):
            all_targets.append(("[T]", tg.name, tg))

        if all_targets:
            for prefix, name, obj_ref in all_targets:
                list_item = QListWidgetItem(f"{prefix} {name}")
                list_item.setData(Qt.ItemDataRole.UserRole, obj_ref)
                self.targets_list.addItem(list_item)
        else:
            list_item = QListWidgetItem("No targets in this clip.")
            list_item.setFlags(list_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.targets_list.addItem(list_item)
            
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

    def handle_delete_targets(self):
        if not self.clip: return

        selected_items = self.targets_list.selectedItems()
        if not selected_items: return
            
        targets_to_delete = [item.data(Qt.ItemDataRole.UserRole) for item in selected_items]
        targets_to_delete = [t for t in targets_to_delete if t is not None]
        if not targets_to_delete: return

        reply = QMessageBox.question(self, 
            'Confirm Target Deletion', 
            f"Are you sure you want to delete {len(targets_to_delete)} selected target(s) from the clip '{self.clip.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, 
            QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Second, ask for the scope
        scope_dialog = DeleteTargetScopeDialog(self.clip.layer, self)
        if not scope_dialog.exec():
            self.main_window.app_logic.log_requested.emit("Target deletion cancelled by user.")
            return

        scope = scope_dialog.get_selected_scope()
        self.main_window.app_logic.process_target_deletion(self.clip, targets_to_delete, scope)


class TransformDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Move/Rotate by Offset")
        main_layout = QVBoxLayout(self)
        
        form_layout = QFormLayout()
        
        self.x_edit = QLineEdit()
        self.y_edit = QLineEdit()
        self.z_edit = QLineEdit()
        self.rot_x_edit = QLineEdit()
        self.rot_y_edit = QLineEdit()
        self.rot_z_edit = QLineEdit()
        
        form_layout.addRow("X Offset:", self.x_edit)
        form_layout.addRow("Y Offset:", self.y_edit)
        form_layout.addRow("Z Offset:", self.z_edit)
        form_layout.addRow(QLabel("--- Rotation (Degrees) ---"))
        form_layout.addRow("X Rotation (Pitch):", self.rot_x_edit)
        form_layout.addRow("Y Rotation (Yaw):", self.rot_y_edit)
        form_layout.addRow("Z Rotation (Roll):", self.rot_z_edit)
        
        main_layout.addLayout(form_layout)
        
        # Rotation Mode Options
        self.rot_mode_group = QGroupBox("Rotation Mode")
        rot_mode_layout = QVBoxLayout()
        self.global_radio = QRadioButton("Global (around world 0,0,0)")
        self.global_radio.setChecked(True)
        self.local_radio = QRadioButton("Local (around character axis)")
        rot_mode_layout.addWidget(self.global_radio)
        rot_mode_layout.addWidget(self.local_radio)
        self.rot_mode_group.setLayout(rot_mode_layout)
        main_layout.addWidget(self.rot_mode_group)
        
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        main_layout.addWidget(buttons)
        
    def set_initial_values(self, x, y, z, rot_x, rot_y, rot_z):
        self.x_edit.setText(f"{x:.4f}")
        self.y_edit.setText(f"{y:.4f}")
        self.z_edit.setText(f"{z:.4f}")
        self.rot_x_edit.setText(f"{rot_x:.2f}")
        self.rot_y_edit.setText(f"{rot_y:.2f}")
        self.rot_z_edit.setText(f"{rot_z:.2f}")

    def get_transform_values(self):
        try:
            pos = (float(self.x_edit.text()), float(self.y_edit.text()), float(self.z_edit.text()))
            rot = (float(self.rot_x_edit.text()), float(self.rot_y_edit.text()), float(self.rot_z_edit.text()))
            return pos, rot
        except ValueError:
            QMessageBox.warning(self, "Invalid Input", "Please enter valid numbers for all offsets and rotations.")
            return None, None

    def get_rotation_mode(self):
        if self.local_radio.isChecked():
            return "local"
        return "global"

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