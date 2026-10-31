# Builds dist\DotaRankTracker.exe (one file, ~200 MB because it carries its own browser for the stats window).
python -m PyInstaller --noconfirm --onefile --windowed --name DotaRankTracker --icon icon.ico `
  --add-data "index.html;." `
  --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtQuick3D --exclude-module PySide6.QtMultimedia `
  --exclude-module PySide6.QtCharts --exclude-module PySide6.QtDataVisualization --exclude-module PySide6.QtPdf `
  rank_overlay.py
