@echo off
rem Baut dist\Skyblock-Flip.exe (einzelne Datei, kein Python noetig)
cd /d "%~dp0"
python -m pip install --quiet --upgrade pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name Skyblock-Flip --icon "%~dp0icon.ico" --add-data "%~dp0icon.ico;." --add-data "%~dp0fonts;fonts" ^
    --workpath build --specpath build skyblock_flip.py
echo.
echo Fertig: dist\Skyblock-Flip.exe
