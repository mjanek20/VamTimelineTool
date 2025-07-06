del build
del dist
pyinstaller --onefile --windowed --icon=Timeliner.ico --add-data "Timeliner.ico;." main.py