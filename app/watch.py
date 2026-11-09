"""Le dossier suivi d'une matière : Pirouette y retourne chercher les cours (à l'ouverture, toutes les heures, ou avec
« Actualiser »). Un cours déjà dans la matière et modifié dans le dossier est mis à jour tout seul ; un fichier
nouveau est proposé (« Ajouter » ou « Ignorer », choix retenu). Les autres documents du dossier (tableurs, images…)
ne sont jamais proposés."""

from __future__ import annotations

from pathlib import Path

from .extract import SUPPORTED_EXTENSIONS


GOOGLE_STUBS = {".gdoc", ".gslides", ".gsheet"}  # Google Drive pour ordinateur : des raccourcis vers le site, pas des fichiers


def google_docs(folder: Path) -> list[str]:
    """Les documents Google Docs / Slides du dossier : Pirouette ne peut pas les lire tels quels."""
    try:
        return sorted(p.name for p in folder.iterdir() if p.suffix.lower() in GOOGLE_STUBS and not p.name.startswith("."))
    except OSError:
        return []


def _course_files(folder: Path) -> tuple[dict[str, Path], list[str]]:
    """Les cours du dossier (premier niveau), par nom en minuscules ; et ceux qu'iCloud a laissés « dans le nuage »."""
    found, cloud = {}, []
    try:
        entries = list(folder.iterdir())
    except OSError:
        return found, cloud
    for path in entries:
        name = path.name
        # iCloud Drive : un fichier pas téléchargé sur ce Mac s'appelle « .Cours.pdf.icloud »
        if name.startswith(".") and name.endswith(".icloud"):
            real = name[1:-len(".icloud")]
            if Path(real).suffix.lower() in SUPPORTED_EXTENSIONS:
                cloud.append(real)
            continue
        if name.startswith((".", "~$")) or Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        # Un .pages ou un .key peut être un dossier (ancien format) : on l'accepte aussi
        if path.is_file() or (path.is_dir() and path.suffix.lower() in {".pages", ".key"}):
            found[name.lower()] = path
    return found, sorted(cloud)


def _mtime(path: Path) -> float:
    try:
        if path.is_dir():  # un .pages en dossier : sa date, c'est celle du fichier le plus récent dedans
            return max((p.stat().st_mtime for p in path.rglob("*") if p.is_file()), default=path.stat().st_mtime)
        return path.stat().st_mtime
    except OSError:
        return 0.0


def read_bytes(path: Path) -> bytes:
    """Le contenu à importer : le fichier, ou un .pages / .key en dossier mis en .zip (comme l'enverrait le Finder)."""
    if not path.is_dir():
        return path.read_bytes()
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for p in path.rglob("*"):
            if p.is_file():
                archive.write(p, p.relative_to(path).as_posix())
    return buffer.getvalue()


def link(course: dict, folder: str) -> dict:
    """Suivre ce dossier : les cours déjà dans la matière qui portent le nom d'un fichier du dossier y sont reliés
    (sans les relire : on part de leur date d'aujourd'hui)."""
    path = Path(folder).expanduser()
    if not path.is_dir():
        raise ValueError("Ce dossier n'existe pas (ou plus).")
    found, _ = _course_files(path)
    watch = {"path": str(path), "ignored": [], "known": {}}
    for f in course["files"]:
        src = found.get(f["name"].lower())
        if src:
            watch["known"][f["name"].lower()] = _mtime(src)
    course["watch"] = watch
    return watch


def scan(course: dict) -> dict:
    """Ce qui a changé dans le dossier depuis la dernière fois : `updated` (cours de la matière modifiés, à relire),
    `new` (fichiers jamais vus, à proposer), `cloud` (dans iCloud, pas sur ce Mac), `missing` (dossier introuvable)."""
    watch = course.get("watch")
    if not watch:
        return {"updated": [], "new": [], "cloud": [], "missing": False}
    folder = Path(watch["path"])
    if not folder.is_dir():
        return {"updated": [], "new": [], "cloud": [], "missing": True}
    found, cloud = _course_files(folder)
    in_course = {f["name"].lower() for f in course["files"]}
    ignored = {n.lower() for n in watch.get("ignored", [])}
    known = watch.setdefault("known", {})
    updated, new = [], []
    for key, path in sorted(found.items()):
        if key in ignored:
            continue
        if key in in_course:
            # Un cours de la matière : relu s'il a changé depuis la dernière fois (ou s'il n'était pas encore relié)
            if key not in known:
                known[key] = _mtime(path)
            elif _mtime(path) > known[key] + 1:
                updated.append(path.name)
        else:
            new.append(path.name)
    return {"updated": updated, "new": new, "cloud": [c for c in cloud if c.lower() not in ignored], "missing": False,
            "google": [g for g in google_docs(folder) if g.lower() not in ignored]}


def take(course: dict, names: list[str]) -> list[tuple[str, bytes]]:
    """Les fichiers à importer (ajoutés ou mis à jour), lus dans le dossier ; leur date est retenue."""
    watch = course["watch"]
    found, _ = _course_files(Path(watch["path"]))
    files = []
    for name in names:
        path = found.get(name.lower())
        if path is None:
            continue
        files.append((path.name, read_bytes(path)))
        watch["known"][name.lower()] = _mtime(path)
    return files


def ignore(course: dict, names: list[str]) -> None:
    ignored = course["watch"].setdefault("ignored", [])
    for name in names:
        if name.lower() not in {n.lower() for n in ignored}:
            ignored.append(name)
