del build
del dist
pyinstaller --onefile --windowed --icon=timeliner-logo.ico --add-data "timeliner-logo.ico;." main.py