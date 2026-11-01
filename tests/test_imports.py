import threading
import time

from app.imports import Imports


def wait(cond, timeout=5):
    end = time.time() + timeout
    while not cond():
        if time.time() > end:
            raise AssertionError("trop long")
        time.sleep(0.01)


def make_run(read, gate):
    """Une « lecture » de 10 pages, qui s'arrête à la page 4 tant que gate n'est pas levé ; les pages lues sont
    gardées dans le cache (comme la reconnaissance de texte d'un scan)."""
    def run(job, files, on_page, caches):
        for page in range(1, 11):
            if str(page) not in caches[0]:
                read.append(page)
                caches[0][str(page)] = f"texte {page}"
            on_page(files[0][0], 0, 1, page, 10, "ocr")
            if page == 4:
                gate.wait(5)
        return {"results": {files[0][0]: "added"}}
    return run


def test_pause_then_resume_continues_where_it_stopped(tmp_path):
    read, gate = [], threading.Event()
    imports = Imports(tmp_path, make_run(read, gate))
    job = imports.start("c1", [("scan.pdf", b"%PDF")], similar=True)
    wait(lambda: imports.get(job["id"])["page"] == 4)
    imports.pause(job["id"])
    gate.set()
    wait(lambda: imports.get(job["id"])["status"] == "paused")
    assert imports.get(job["id"])["page"] == 4
    imports.resume(job["id"])
    wait(lambda: imports.get(job["id"])["status"] == "done")
    assert read == list(range(1, 11))  # chaque page lue une seule fois
    assert imports.get(job["id"])["result"] == {"results": {"scan.pdf": "added"}}
    assert imports.active("c1") is None


def test_a_job_left_running_is_paused_after_a_restart(tmp_path):
    read, gate = [], threading.Event()
    imports = Imports(tmp_path, make_run(read, gate))
    job = imports.start("c1", [("scan.pdf", b"%PDF")], similar=False)
    wait(lambda: imports.get(job["id"])["page"] == 4)
    # Pirouette fermée en plein import : au redémarrage, la tâche attend qu'on la reprenne
    again = Imports(tmp_path, make_run(read, threading.Event()))
    assert again.active("c1")["status"] == "paused"
    gate.set()
