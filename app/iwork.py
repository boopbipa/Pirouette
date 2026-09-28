"""Lecture directe du texte des fichiers Pages et Keynote (format iWork 2013 et suivants), sans l'app Apple.

Un fichier .pages / .key est une archive zip. Le contenu est dans des fichiers « .iwa » :
des blocs compressés en Snappy, contenant une suite de messages Protobuf. Le texte du document
se trouve dans les messages TSWP.StorageArchive (type 2001), champ 3.
"""

from __future__ import annotations

import io
import zipfile

STORAGE_ARCHIVE = 2001  # TSWP.StorageArchive : un bloc de texte (corps, zone de texte, note…)
KIND_FIELD, TEXT_FIELD = 1, 3
KIND_NAMES = {0: "corps", 1: "en-tête", 2: "note de bas de page", 3: "zone de texte", 4: "note",
              5: "cellule", 6: "autre", 7: "cellule"}


class IWorkError(ValueError):
    pass


# ---------- Snappy (format brut, sans somme de contrôle) ----------

def _varint(data: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if pos >= len(data):
            raise IWorkError("varint tronqué")
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


def snappy_decompress(data: bytes) -> bytes:
    length, pos = _varint(data, 0)
    out = bytearray()
    while pos < len(data):
        tag = data[pos]
        pos += 1
        kind = tag & 3
        if kind == 0:  # littéral
            size = tag >> 2
            if size >= 60:
                extra = size - 59
                size = int.from_bytes(data[pos:pos + extra], "little")
                pos += extra
            size += 1
            out += data[pos:pos + size]
            pos += size
            continue
        if kind == 1:
            size = ((tag >> 2) & 7) + 4
            offset = ((tag >> 5) << 8) | data[pos]
            pos += 1
        elif kind == 2:
            size = (tag >> 2) + 1
            offset = int.from_bytes(data[pos:pos + 2], "little")
            pos += 2
        else:
            size = (tag >> 2) + 1
            offset = int.from_bytes(data[pos:pos + 4], "little")
            pos += 4
        if offset == 0 or offset > len(out):
            raise IWorkError("copie Snappy invalide")
        start = len(out) - offset
        for i in range(size):  # copie octet par octet : les zones peuvent se chevaucher
            out.append(out[start + i])
    if len(out) != length:
        raise IWorkError("taille Snappy inattendue")
    return bytes(out)


def iwa_stream(data: bytes) -> bytes:
    """Un .iwa = blocs [0x00][longueur sur 3 octets][données Snappy]."""
    out = bytearray()
    pos = 0
    while pos < len(data):
        if data[pos] != 0:
            raise IWorkError("bloc IWA inconnu")
        size = int.from_bytes(data[pos + 1:pos + 4], "little")
        out += snappy_decompress(data[pos + 4:pos + 4 + size])
        pos += 4 + size
    return bytes(out)


# ---------- Protobuf (lecture générique, sans schéma) ----------

def _fields(message: bytes):
    """Parcourt un message Protobuf : renvoie (numéro, type, valeur)."""
    pos = 0
    while pos < len(message):
        key, pos = _varint(message, pos)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = _varint(message, pos)
        elif wire == 1:
            value, pos = message[pos:pos + 8], pos + 8
        elif wire == 2:
            size, pos = _varint(message, pos)
            value, pos = message[pos:pos + size], pos + size
        elif wire == 5:
            value, pos = message[pos:pos + 4], pos + 4
        else:
            raise IWorkError("type Protobuf inconnu")
        yield number, wire, value


def iwa_messages(stream: bytes):
    """Renvoie (type de message, contenu) pour chaque objet de l'archive."""
    pos = 0
    while pos < len(stream):
        size, pos = _varint(stream, pos)
        info, pos = stream[pos:pos + size], pos + size
        infos = [value for number, wire, value in _fields(info) if number == 2 and wire == 2]
        for message_info in infos:
            kind = length = 0
            for number, _, value in _fields(message_info):
                if number == 1:
                    kind = value
                elif number == 3:
                    length = value
            yield kind, stream[pos:pos + length]
            pos += length


def iwa_objects(stream: bytes):
    """Renvoie (identifiant, type, contenu) de chaque objet de l'archive (son message principal)."""
    pos = 0
    while pos < len(stream):
        size, pos = _varint(stream, pos)
        info, pos = stream[pos:pos + size], pos + size
        identifier, infos = None, []
        for number, wire, value in _fields(info):
            if number == 1 and wire == 0:
                identifier = value
            elif number == 2 and wire == 2:
                infos.append(value)
        for position, message_info in enumerate(infos):
            kind = length = 0
            for number, _, value in _fields(message_info):
                if number == 1:
                    kind = value
                elif number == 3:
                    length = value
            if position == 0:
                yield identifier, kind, stream[pos:pos + length]
            pos += length


# ---------- Mise en forme : gras / italique (pour repérer titres et définitions) ----------

PARAGRAPH_STYLE, CHARACTER_STYLE = 2022, 2021  # TSWP.ParagraphStyleArchive, TSWP.CharacterStyleArchive
PARA_STYLE_TABLE, CHAR_STYLE_TABLE = 5, 8      # champs de TSWP.StorageArchive (tables « position → style »)


def _reference(message: bytes) -> int | None:
    """TSP.Reference : identifiant de l'objet visé (champ 1)."""
    return next((value for number, wire, value in _fields(message) if number == 1 and wire == 0), None)


def _attribute_table(message: bytes) -> list[tuple[int, int | None]]:
    """TSWP.ObjectAttributeTable : liste (position du caractère, style ou None), dans l'ordre."""
    entries = []
    for number, wire, entry in _fields(message):
        if number == 1 and wire == 2:
            index, ref = 0, None
            for n, w, value in _fields(entry):
                if n == 1 and w == 0:
                    index = value
                elif n == 2 and w == 2:
                    ref = _reference(value)
            entries.append((index, ref))
    return sorted(entries, key=lambda e: e[0])


class _Styles:
    """Gras / italique d'un style Pages, en tenant compte des styles parents."""

    def __init__(self, objects: dict[int, tuple[int, bytes]]):
        self.objects = objects
        self.cache: dict[int, dict] = {}

    def props(self, ident: int | None, depth: int = 0) -> dict:
        if ident is None or depth > 20:
            return {}
        if ident in self.cache:
            return self.cache[ident]
        kind, payload = self.objects.get(ident, (0, b""))
        own: dict = {}
        parent = None
        if kind in (PARAGRAPH_STYLE, CHARACTER_STYLE):
            for number, wire, value in _fields(payload):
                if number == 1 and wire == 2:  # TSS.StyleArchive : le style parent (champ 3)
                    parent = next((_reference(v) for n, w, v in _fields(value) if n == 3 and w == 2), None)
                elif number == 11 and wire == 2:  # CharacterStylePropertiesArchive : gras (1), italique (2)
                    for n, w, v in _fields(value):
                        if n == 1 and w == 0:
                            own["bold"] = bool(v)
                        elif n == 2 and w == 0:
                            own["italic"] = bool(v)
        result = (self.props(parent, depth + 1) if parent not in (None, ident) else {}) | own
        self.cache[ident] = result
        return result


def _utf16_positions(text: str) -> list[int]:
    """Les positions iWork comptent en UTF-16 : correspondance vers les positions Python."""
    positions, unit = [], 0
    for index, char in enumerate(text):
        positions.append(unit)
        unit += 2 if ord(char) > 0xFFFF else 1
    return positions


def _markdown(text: str, para_table, char_table, styles: _Styles) -> str:
    """Texte avec le gras et l'italique en Markdown (**gras**, *italique*, ***les deux***), paragraphe par paragraphe."""
    if not para_table and not char_table:
        return text
    units = _utf16_positions(text)

    def style_at(table, unit):
        current = None
        for index, ref in table:
            if index > unit:
                break
            current = ref
        return current

    def emphasis(i: int, para_props: dict) -> tuple[bool, bool]:
        char_props = styles.props(style_at(char_table, units[i]))
        bold = char_props.get("bold", para_props.get("bold", False))
        italic = char_props.get("italic", para_props.get("italic", False))
        return bold, italic

    out = []
    start = 0
    for end in [i for i, c in enumerate(text) if c in "\n\u2029"] + [len(text)]:
        paragraph = text[start:end]
        if paragraph.strip():
            para_props = styles.props(style_at(para_table, units[start]))
            flags = [emphasis(start + i, para_props) for i in range(len(paragraph))]
            out.append(_wrap_runs(paragraph, flags))
        else:
            out.append(paragraph)
        if end < len(text):
            out.append(text[end])
        start = end + 1
    return "".join(out)


def _wrap_runs(paragraph: str, flags: list[tuple[bool, bool]]) -> str:
    visible = [f for c, f in zip(paragraph, flags) if c.strip() and c != "\ufffc"]
    if visible and all(f == visible[0] for f in visible):  # tout le paragraphe dans la même mise en forme
        return _wrap(paragraph, *visible[0])
    pieces, run, current = [], "", None
    for char, flag in zip(paragraph, flags):
        if flag != current and run:
            pieces.append(_wrap(run, *current))
            run = ""
        current = flag
        run += char
    if run:
        pieces.append(_wrap(run, *current))
    return "".join(pieces)


def _wrap(text: str, bold: bool, italic: bool) -> str:
    marker = "***" if bold and italic else "**" if bold else "*" if italic else ""
    core = text.strip()
    if not marker or not core:
        return text
    left, right = text[:len(text) - len(text.lstrip())], text[len(text.rstrip()):]
    return f"{left}{marker}{core}{marker}{right}"


# ---------- Texte ----------

def extract_iwork_text(data: bytes) -> str:
    """Texte d'un fichier Pages / Keynote récent, avec le gras et l'italique en Markdown.
    Lève IWorkError si le format n'est pas reconnu."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        if "Index.zip" in archive.namelist():  # variante « paquet » : les .iwa sont dans un Index.zip imbriqué
            archive = zipfile.ZipFile(io.BytesIO(archive.read("Index.zip")))
    except zipfile.BadZipFile as exc:
        raise IWorkError("pas une archive iWork") from exc
    all_names = [n for n in archive.namelist() if n.startswith("Index/") and n.endswith(".iwa")]
    # MasterSlide* : modèles de diapositives Keynote (« Title Text », « Body Level One »…), pas du contenu.
    names = [n for n in all_names if not n.startswith("Index/MasterSlide")]
    if not names:
        raise IWorkError("aucun fichier .iwa")
    # Le document principal d'abord (Document.iwa), puis les diapos Keynote (Slide*.iwa) dans l'ordre.
    names.sort(key=lambda n: (n != "Index/Document.iwa", _natural(n)))

    objects: dict[int, tuple[int, bytes]] = {}
    order: dict[str, list[int]] = {}
    for name in all_names:
        try:
            for ident, kind, payload in iwa_objects(iwa_stream(archive.read(name))):
                objects[ident] = (kind, payload)
                order.setdefault(name, []).append(ident)
        except IWorkError:
            continue
    styles = _Styles(objects)

    blocks: list[str] = []
    seen: set[str] = set()
    for name in names:
        for ident in order.get(name, []):
            kind, payload = objects[ident]
            if kind != STORAGE_ARCHIVE:
                continue
            storage_kind, parts, para_table, char_table = 0, [], [], []
            try:
                for number, wire, value in _fields(payload):
                    if number == KIND_FIELD and wire == 0:
                        storage_kind = value
                    elif number == TEXT_FIELD and wire == 2:
                        parts.append(value.decode("utf-8", errors="replace"))
                    elif number == PARA_STYLE_TABLE and wire == 2:
                        para_table = _attribute_table(value)
                    elif number == CHAR_STYLE_TABLE and wire == 2:
                        char_table = _attribute_table(value)
            except IWorkError:
                continue
            raw = "".join(parts)
            plain = _tidy(raw)
            if not plain or storage_kind in (5, 7) or plain in seen:  # les cellules de tableau sont lues à part
                continue
            seen.add(plain)
            try:
                blocks.append(_tidy(_markdown(raw, para_table, char_table, styles)))
            except (IWorkError, IndexError):
                blocks.append(plain)
    return "\n\n".join(blocks)


def _tidy(text: str) -> str:
    # Séparateurs de paragraphe / saut de ligne iWork, et caractères d'objets ancrés (U+FFFC).
    text = text.replace(" ", "\n").replace(" ", "\n").replace("￼", "").replace("\x00", "")
    return text.strip()


def _natural(name: str):
    import re

    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name)]


# ---------- Ancien format (iWork '09) : texte en XML ----------

def extract_legacy_xml_text(data: bytes) -> str:
    """Texte d'un fichier Pages / Keynote d'avant 2013 (index.xml ou index.apxl, parfois compressé en gzip)."""
    import gzip
    import xml.etree.ElementTree as ET

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise IWorkError("pas une archive iWork") from exc
    name = next((n for n in ("index.xml", "index.apxl", "index.xml.gz", "index.apxl.gz") if n in archive.namelist()), None)
    if name is None:
        raise IWorkError("pas de document XML")
    raw = archive.read(name)
    if name.endswith(".gz"):
        raw = gzip.decompress(raw)

    paragraphs: list[str] = []
    in_master = 0
    try:
        for event, element in ET.iterparse(io.BytesIO(raw), events=("start", "end")):
            local = element.tag.rsplit("}", 1)[-1]
            if local in ("master-slide", "master-slides"):  # modèles de diapositives Keynote
                in_master += 1 if event == "start" else -1
            elif event == "end" and local == "p":
                if not in_master:
                    text = _tidy("".join(element.itertext()))
                    if text:
                        paragraphs.append(text)
                element.clear()
    except ET.ParseError as exc:
        raise IWorkError("XML illisible") from exc
    return "\n".join(paragraphs)
