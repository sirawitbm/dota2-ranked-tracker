# Builds dist\DotaRankTracker.exe (one file). What goes in, and what's left out, is in DotaRankTracker.spec.
python -m PyInstaller --noconfirm DotaRankTracker.spec
