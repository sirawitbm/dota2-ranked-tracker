# Builds dist\DotaRankTracker.exe (one file, ~300 MB: its own browser for the stats window plus the OCR models).
# RapidOCR fetches its models on first use; do that now so they get packed into the exe.
python -c "from rapidocr import RapidOCR; RapidOCR()"
python -m PyInstaller --noconfirm --onefile --windowed --name DotaRankTracker --icon icon.ico `
  --add-data "index.html;." --collect-data rapidocr `
  --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtQuick3D --exclude-module PySide6.QtMultimedia `
  --exclude-module PySide6.QtCharts --exclude-module PySide6.QtDataVisualization --exclude-module PySide6.QtPdf `
  --exclude-module winrt --exclude-module torch --exclude-module paddle --exclude-module openvino --exclude-module tensorrt `
  rank_overlay.py
