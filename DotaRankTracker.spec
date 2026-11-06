# PyInstaller build: python -m PyInstaller --noconfirm DotaRankTracker.spec  (or run build.ps1)
# Same as a plain --onefile build, minus Qt/Python parts the app never uses (~20 MB).
a = Analysis(
    ["rank_overlay.py"],
    datas=[("models/PP-OCRv6_rec_tiny.onnx", "models")],
    excludes=["rapidocr", "cv2", "PIL", "winrt"],
)

# software OpenGL, QML/Quick, PDF, SVG, Qt's network/TLS, image formats, translations
# (Python's own ssl stays: hero portraits are downloaded over https)
DROP = ("opengl32sw", "Qt6Network", "QtNetwork", "Qt6Pdf", "Qt6Qml", "Qt6Quick", "Qt6Svg", "Qt6VirtualKeyboard",
        "plugins/generic", "plugins/iconengines", "plugins/imageformats", "plugins/networkinformation",
        "plugins/platforminputcontexts", "qdirect2d", "qminimal", "qoffscreen", "plugins/tls", "translations")
a.binaries = [e for e in a.binaries if not any(d in e[0].replace("\\", "/") for d in DROP)]
a.datas = [e for e in a.datas if not any(d in e[0].replace("\\", "/") for d in DROP)]

pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, name="DotaRankTracker", icon="icon.ico", console=False, upx=False)
