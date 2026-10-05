# Recette PyInstaller de Pirouette.app — lancée par packaging/build_macos.sh
# -*- mode: python ; coding: utf-8 -*-
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent
VERSION = re.search(r'__version__ = "(.+)"', (ROOT / "app" / "__init__.py").read_text()).group(1)

a = Analysis(
    [str(ROOT / "desktop.py")],
    pathex=[str(ROOT)],
    # online_config.json : adresse et clé publique du service en ligne (défis), ajoutées par GitHub à la fabrication
    datas=[(str(ROOT / "app" / "static"), "app/static"),
           *([(str(ROOT / "app" / "online_config.json"), "app")] if (ROOT / "app" / "online_config.json").exists() else []),
           *collect_data_files("docx"), *collect_data_files("pptx"),
           *collect_data_files("pypdfium2"), *collect_data_files("pypdfium2_raw")],
    # pdfium (rendu des pages de PDF en image, pour montrer les figures à Claude) : bibliothèque native.
    binaries=collect_dynamic_libs("pypdfium2_raw"),
    # uvicorn charge sa boucle et son protocole HTTP par leur nom : on les inclut explicitement.
    hiddenimports=[*collect_submodules("app"), *collect_submodules("uvicorn"), "python_multipart",
                   *collect_submodules("pypdfium2"), "pypdfium2_raw"],
    excludes=["tkinter", "pytest", "playwright", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Pirouette",
    console=False,
    argv_emulation=False,
    codesign_identity=None,
    icon=str(ROOT / "packaging" / "Pirouette.ico") if sys.platform == "win32" else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Pirouette")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Pirouette.app",
        icon=str(ROOT / "packaging" / "Pirouette.icns"),
        bundle_identifier="fr.pirouette.app",
        version=VERSION,
        info_plist={
            "CFBundleName": "Pirouette",
            "CFBundleDisplayName": "Pirouette",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSMinimumSystemVersion": "11.0",
            "LSApplicationCategoryType": "public.app-category.education",
            "NSHighResolutionCapable": True,
            # Demandé par macOS pour convertir les fichiers Pages / Keynote via ces apps
            "NSAppleEventsUsageDescription": "Pirouette utilise Pages et Keynote pour lire tes cours enregistrés dans ces formats.",
            "NSHumanReadableCopyright": "Pirouette — tes cours deviennent des quiz.",
        },
    )
