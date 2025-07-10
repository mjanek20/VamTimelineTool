# ui_styles.py

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