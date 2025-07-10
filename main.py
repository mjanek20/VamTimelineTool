# main.py
import sys
import os
from collections import defaultdict

from PyQt6.QtCore import Qt, QDateTime, QSettings
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QFileDialog, QLabel, QMessageBox, QInputDialog, QToolBar,
    QPlainTextEdit, QPushButton, QTreeWidgetItem, QStyle,
    QLineEdit
)

# Importy z naszych modułów
from ui_styles import DARK_STYLE
from data_models import AnimationClip
from ui_components import (
    AnimationTreeWidget, ClipPropertiesPanel, MergeConflictDialog,
    BatchRenameDialog, OffsetDialog
)
from app_logic import AppLogic, MergeError

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.app_logic = AppLogic()
        self.is_first_load = True

        self.setWindowTitle("Timeliner")
        ico_path = os.path.join(getattr(sys, '_MEIPASS', os.path.abspath('.')), 'timeliner-logo.ico')
        if os.path.exists(ico_path): self.setWindowIcon(QIcon(ico_path))
        
        self.setGeometry(100, 100, 1200, 800)
        self.settings = QSettings("VamTimelineTools", "TimelinerEditor")
        self.last_directory = self.settings.value("last_directory", "")
        
        self.init_ui()
        self.connect_signals()
        
        self.log_message("Application started.")
        
        is_dark = self.settings.value("darkModeEnabled", True, type=bool)
        self.dark_mode_action.setChecked(is_dark)
        self.apply_styles(is_dark)

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
        left_panel=QWidget();left_layout=QVBoxLayout(left_panel);left_panel.setFixedWidth(400);filter_layout=QHBoxLayout();self.filter_edit = QLineEdit();self.filter_edit.setPlaceholderText("Filter animations...");self.filter_edit.textChanged.connect(self.filter_tree);filter_layout.addWidget(self.filter_edit)
        self.fold_all_button=QPushButton("Fold All");self.fold_all_button.clicked.connect(self.fold_all_items);filter_layout.addWidget(self.fold_all_button)
        self.unfold_all_button=QPushButton("Unfold All");self.unfold_all_button.clicked.connect(self.unfold_all_items);filter_layout.addWidget(self.unfold_all_button)
        left_layout.addLayout(filter_layout)
        self.tree=AnimationTreeWidget(self);self.tree.setHeaderLabels(["Atom / Segment / Layer / Animation"]);self.tree.itemSelectionChanged.connect(self.on_tree_selection_changed);self.tree.itemChanged.connect(self.on_item_renamed);left_layout.addWidget(self.tree)
        right_panel=QWidget();right_layout=QVBoxLayout(right_panel)
        self.placeholder_label=QLabel("Select a clip to see its properties.");self.placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.properties_panel=ClipPropertiesPanel(self)
        self.log_console=QPlainTextEdit();self.log_console.setReadOnly(True);self.log_console.setFixedHeight(150);self.log_console.setObjectName("LogConsole")
        right_layout.addWidget(self.placeholder_label);right_layout.addWidget(self.properties_panel);right_layout.addStretch(1);right_layout.addWidget(QLabel("<b>Console Log</b>"));right_layout.addWidget(self.log_console)
        main_layout.addWidget(left_panel);main_layout.addWidget(right_panel)

    def connect_signals(self):
        self.app_logic.file_changed.connect(self.on_file_changed)
        self.app_logic.clips_updated.connect(self.populate_animation_tree)
        self.app_logic.log_requested.connect(self.log_message)
        self.app_logic.error_occurred.connect(self.show_error_message)

    def on_file_changed(self, file_path):
        title = f"Timeliner - {file_path}" if file_path else "Timeliner"
        self.setWindowTitle(title)
        
        if file_path and not file_path.endswith("*"):
            clean_path = self.app_logic.current_file_path.replace(" *", "")
            if os.path.exists(clean_path):
                 self.last_directory = os.path.dirname(clean_path)
                 self.settings.setValue("last_directory", self.last_directory)
        
        self.is_first_load = True # Treat every new file load as a "first load" for expansion
        self.populate_animation_tree()

    def get_tree_state(self):
        """Saves the expansion state of the tree."""
        state = set()
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.isExpanded():
                data = item.data(0, 1000)
                if data: state.add(data)
            self._get_tree_state_recursive(item, state)
        return state

    def _get_tree_state_recursive(self, parent_item, state):
        for i in range(parent_item.childCount()):
            item = parent_item.child(i)
            if item.isExpanded():
                data = item.data(0, 1000)
                if data: state.add(data)
            self._get_tree_state_recursive(item, state)

    def populate_animation_tree(self):
        self.tree.blockSignals(True)
        
        try:
            expansion_state = self.get_tree_state() if not self.is_first_load else None
            
            current_selection_key = None
            selected_items = self.tree.selectedItems()
            if selected_items:
                data = selected_items[0].data(0, 1000)
                current_selection_key = id(data) if isinstance(data, AnimationClip) else data

            self.tree.clear()
            
            animation_file = self.app_logic.animation_file
            if not animation_file:
                self.on_tree_selection_changed()
                return

            root_item = self.tree.invisibleRootItem()
            self.tree.setHeaderLabels(["Atom / Segment / Layer / Animation" if animation_file.is_scene else "Segment / Layer / Animation"])

            new_item_to_select = self._populate_recursive(root_item, animation_file.clips, current_selection_key, expansion_state)
            
            if self.is_first_load:
                self.tree.expandAll()
                self.is_first_load = False

            if new_item_to_select:
                self.tree.setCurrentItem(new_item_to_select)

        finally:
            self.tree.blockSignals(False)
        
        self.on_tree_selection_changed()
    
    def _populate_recursive(self, parent_item, clips, selection_key, expansion_state):
        item_to_reselect = None
        if not self.app_logic.animation_file: return None
        is_scene = self.app_logic.animation_file.is_scene
        
        # Atom level (only for scene files and root parent)
        if is_scene and parent_item == self.tree.invisibleRootItem():
            grouped = defaultdict(list)
            for clip in clips: grouped[clip.atom_id].append(clip)
            for atom_id, atom_clips in sorted(grouped.items()):
                atom_item_data = ("atom", atom_id)
                atom_item = QTreeWidgetItem(parent_item, [f"Atom: {atom_id}"])
                atom_item.setData(0, 1000, atom_item_data)
                atom_item.setFlags(atom_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if expansion_state and atom_item_data in expansion_state:
                    atom_item.setExpanded(True)
                if atom_item_data == selection_key: item_to_reselect = atom_item
                result = self._populate_recursive(atom_item, atom_clips, selection_key, expansion_state)
                if result: item_to_reselect = result
            return item_to_reselect

        # Segment level
        grouped = defaultdict(list)
        for clip in clips: grouped[clip.segment].append(clip)
        for seg_name, seg_clips in sorted(grouped.items()):
            atom_id_data = parent_item.data(0, 1000)
            atom_id = atom_id_data[1] if isinstance(atom_id_data, tuple) and atom_id_data[0] == 'atom' else "(Standalone)"
            seg_item_data = ("segment", atom_id, seg_name)
            seg_item = QTreeWidgetItem(parent_item, [f"Segment: {seg_name}"])
            seg_item.setData(0, 1000, seg_item_data)
            seg_item.setFlags(seg_item.flags() | Qt.ItemFlag.ItemIsEditable)
            if expansion_state and seg_item_data in expansion_state:
                seg_item.setExpanded(True)
            if seg_item_data == selection_key: item_to_reselect = seg_item

            # Layer level
            layer_grouped = defaultdict(list)
            for clip in seg_clips: layer_grouped[clip.layer].append(clip)
            for layer_name, layer_clips in sorted(layer_grouped.items()):
                layer_item_data = ("layer", atom_id, seg_name, layer_name)
                layer_item = QTreeWidgetItem(seg_item, [f"  Layer: {layer_name}"])
                layer_item.setData(0, 1000, layer_item_data)
                layer_item.setFlags(layer_item.flags() | Qt.ItemFlag.ItemIsEditable)
                if expansion_state and layer_item_data in expansion_state:
                    layer_item.setExpanded(True)
                if layer_item_data == selection_key: item_to_reselect = layer_item
                
                # Clip level
                for clip_obj in sorted(layer_clips, key=lambda c: c.order_index):
                    clip_item = QTreeWidgetItem(layer_item, [f"    Clip: {clip_obj.name}"])
                    clip_item.setData(0, 1000, clip_obj)
                    clip_item.setFlags(clip_item.flags() | Qt.ItemFlag.ItemIsEditable)
                    if selection_key and isinstance(selection_key, int) and id(clip_obj) == selection_key: 
                        item_to_reselect = clip_item
        return item_to_reselect

    def log_message(self, message):
        timestamp = QDateTime.currentDateTime().toString("hh:mm:ss")
        self.log_console.appendPlainText(f"[{timestamp}] {message}")

    def show_error_message(self, title, message):
        QMessageBox.critical(self, title, message)
        self.log_message(f"ERROR: {title} - {message}")

    def open_file(self):
        file_name, _ = QFileDialog.getOpenFileName(self, "Open Animation or Scene File", self.last_directory, "JSON Files (*.json)")
        if not file_name: 
            return

        if self.app_logic.animation_file:
            msg_box = QMessageBox(self)
            msg_box.setIcon(QMessageBox.Icon.Question)
            msg_box.setText("A file is already open.")
            msg_box.setInformativeText("Do you want to merge the new file into the current one, or replace it?")
            
            merge_btn = msg_box.addButton("Merge", QMessageBox.ButtonRole.ActionRole)
            replace_btn = msg_box.addButton("Replace", QMessageBox.ButtonRole.ActionRole)
            msg_box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            
            msg_box.exec()
            
            clicked_btn = msg_box.clickedButton()
            if clicked_btn == merge_btn:
                self.handle_merge_file(file_name)
            elif clicked_btn == replace_btn:
                self.app_logic.load_file(file_name)
            else: # Cancel
                return
        else:
            self.app_logic.load_file(file_name)

    def handle_merge_file(self, file_to_merge):
        try:
            conflict_dialog = MergeConflictDialog(self)
            if not conflict_dialog.exec():
                self.log_message("Merge cancelled by user.")
                return
            
            strategy = conflict_dialog.get_selected_strategy()
            self.app_logic.merge_animation_file(file_to_merge, strategy)

        except MergeError as e:
            self.show_error_message("Merge Error", str(e))
        except Exception as e:
            self.show_error_message("Unexpected Error", f"An unexpected error occurred during merge: {e}")

    def save_file_as(self):
        if not self.app_logic.animation_file:
            self.log_message("No data loaded to save.")
            return
        
        current_path = (self.app_logic.current_file_path or "").replace(" *", "")
        start_path = self.last_directory or current_path or ""
        file_name, _ = QFileDialog.getSaveFileName(self, "Save As", start_path, "JSON Files (*.json)")
        if file_name:
            if not file_name.lower().endswith('.json'): file_name += '.json'
            self.app_logic.save_file(file_name)

    def delete_selected_items(self):
        selected_data = [item.data(0, 1000) for item in self.tree.selectedItems()]
        if not selected_data: return

        reply = QMessageBox.question(self, 'Confirm Deletion', f"Delete {len(selected_data)} selected item(s)?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.app_logic.delete_items(selected_data)

    def center_root_on_first_frame(self):
        selected_clips = [item.data(0, 1000) for item in self.tree.selectedItems() if isinstance(item.data(0, 1000), AnimationClip)]
        if not selected_clips:
            QMessageBox.warning(self, "No Selection", "Please select one or more clips to process.")
            return
        self.app_logic.center_root_on_first_frame(selected_clips)
        
    def move_root_by_offset(self):
        selected_clips = [item.data(0, 1000) for item in self.tree.selectedItems() if isinstance(item.data(0, 1000), AnimationClip)]
        if not selected_clips:
            QMessageBox.warning(self, "Invalid Selection", "Please select valid animation clips.")
            return

        dialog = OffsetDialog(self)
        last_delta = self.app_logic.last_center_root_delta_xz
        dialog.set_initial_values(last_delta[0], 0.0, last_delta[1])
        
        if dialog.exec():
            offsets = dialog.get_offsets()
            if offsets:
                self.app_logic.move_root_by_offset(selected_clips, offsets)

    def rename_selected_item(self):
        if self.tree.currentItem():
            self.tree.editItem(self.tree.currentItem(), 0)

    def on_item_renamed(self, item, col):
        data = item.data(0, 1000)
        new_text_raw = item.text(0)
        
        prefix_map = {"Segment: ": "", "  Layer: ": "", "    Clip: ": ""}
        new_text = new_text_raw
        for prefix in prefix_map:
            if new_text_raw.startswith(prefix):
                new_text = new_text_raw.replace(prefix, "", 1)
                break
        
        self.app_logic.rename_item(data, new_text.strip())

    def create_new_segment(self):
        if not self.app_logic.animation_file:
            self.log_message("Action failed: No data loaded.")
            return
        text, ok = QInputDialog.getText(self, 'New Segment', 'Enter new segment name:')
        if ok and text:
            self.app_logic.create_new_segment(text)

    def duplicate_selected_clip(self):
        item = self.tree.currentItem()
        if not item or not isinstance(item.data(0, 1000), AnimationClip):
            self.log_message("Please select a single clip to duplicate.")
            return
        self.app_logic.duplicate_clip(item.data(0, 1000))

    def batch_rename_items(self):
        selected_clips = [item.data(0, 1000) for item in self.tree.selectedItems() if isinstance(item.data(0, 1000), AnimationClip)]
        if not selected_clips:
            QMessageBox.information(self, "Info", "Select clips to rename.")
            return
        
        dialog = BatchRenameDialog(self)
        if dialog.exec():
            params = dialog.get_params()
            self.app_logic.batch_rename_clips(selected_clips, **params)
        
    def on_tree_selection_changed(self):
        selected = self.tree.selectedItems()
        if selected and isinstance(selected[0].data(0, 1000), AnimationClip):
            self.properties_panel.display_clip_properties(selected[0].data(0, 1000), selected[0])
            self.placeholder_label.hide()
        else:
            self.properties_panel.clear()
            self.placeholder_label.show()

    def filter_tree(self, text):
        search_text = text.lower()
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            self._filter_recursive(root.child(i), search_text)

    def _filter_recursive(self, item, search_text):
        item_text_visible = search_text in item.text(0).lower()
        child_visible = False
        for i in range(item.childCount()):
            if self._filter_recursive(item.child(i), search_text):
                child_visible = True
        
        is_visible = item_text_visible or child_visible
        item.setHidden(not is_visible)
        if search_text and is_visible:
            parent = item.parent()
            while parent:
                parent.setExpanded(True)
                parent = parent.parent()
        return is_visible

    def fold_all_items(self):
        self.tree.collapseAll()
    
    def unfold_all_items(self):
        self.tree.expandAll()
    
    def toggle_dark_mode(self, checked):
        self.settings.setValue("darkModeEnabled", checked)
        self.apply_styles(checked)
        self.log_message(f"Dark Mode {'Enabled' if checked else 'Disabled'}.")

    def apply_styles(self, is_dark):
        if is_dark:
            QApplication.instance().setStyleSheet(DARK_STYLE)
        else:
            QApplication.instance().setStyleSheet("")
        self.update_toolbar_icons()

    def update_toolbar_icons(self):
        style = self.style()
        self.open_action.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton))
        self.save_as_action.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        self.new_segment_action.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_FileDialogNewFolder))
        self.delete_action.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
        
# --- Application Entry Point ---
if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())