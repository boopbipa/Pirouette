def split_all(client, course_id):
    """Découpe en chapitres (repérage automatique, sans IA) tous les fichiers d'un cours : depuis la 0.43.4, un fichier
    importé reste d'un seul bloc tant qu'on ne demande pas le découpage."""
    for f in client.get(f"/api/courses/{course_id}").json()["files"]:
        client.post(f"/api/courses/{course_id}/files/{f['id']}/chapters/auto")
