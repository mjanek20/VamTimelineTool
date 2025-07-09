del build
del dist
pyinstaller --noupx --onefile --windowed --icon=timeliner-logo.ico --version-file=version.txt --add-data "timeliner-logo.ico;." main.py