import json

from app.storage import SUBJECT_COLORS, Store


def test_each_subject_gets_its_own_color(tmp_path):
    store = Store(tmp_path)
    colors = [store.create_course(f"Matière {i}")["color"] for i in range(SUBJECT_COLORS)]
    assert sorted(colors) == list(range(SUBJECT_COLORS))  # toutes différentes tant qu'il en reste
    extra = store.create_course("Une de plus")["color"]
    assert extra in range(SUBJECT_COLORS)


def test_old_subjects_get_free_colors(tmp_path):
    store = Store(tmp_path)
    ids = [store.create_course(name)["id"] for name in ("A", "B", "C")]
    for course_id in ids:  # matières d'avant les couleurs
        path = store.courses_dir / course_id / "course.json"
        course = json.loads(path.read_text())
        course.pop("color")
        path.write_text(json.dumps(course))
    colors = [c["color"] for c in store.list_courses()]
    assert len(set(colors)) == 3
    assert [c["color"] for c in store.list_courses()] == colors  # et elles ne changent plus
