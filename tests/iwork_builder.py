"""Fabrique de petits fichiers Pages (format IWA) pour les tests : texte + styles gras / italique."""

import io
import zipfile


def varint(n: int) -> bytes:
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def field(number: int, value) -> bytes:
    if isinstance(value, bool) or isinstance(value, int):
        return varint(number << 3) + varint(int(value))
    data = value.encode() if isinstance(value, str) else value
    return varint(number << 3 | 2) + varint(len(data)) + data


def ref(ident: int) -> bytes:
    return field(1, ident)


def snappy(data: bytes) -> bytes:
    """Compression Snappy « sans compression » : uniquement des blocs littéraux."""
    out = bytearray(varint(len(data)))
    for i in range(0, len(data), 256):
        chunk = data[i:i + 256]
        out += bytes([(len(chunk) - 1) << 2]) if len(chunk) <= 60 else bytes([60 << 2, len(chunk) - 1])
        out += chunk
    return bytes(out)


def iwa(objects: list[tuple[int, int, bytes]]) -> bytes:
    stream = bytearray()
    for ident, kind, payload in objects:
        info = field(1, ident) + field(2, field(1, kind) + field(3, len(payload)))
        stream += varint(len(info)) + info + payload
    body = snappy(bytes(stream))
    return b"\x00" + len(body).to_bytes(3, "little") + body


def para_style(name: str, bold=None, italic=None, parent: int | None = None) -> bytes:
    style = field(1, name) + (field(3, ref(parent)) if parent else b"")
    props = (field(1, bold) if bold is not None else b"") + (field(2, italic) if italic is not None else b"")
    return field(1, style) + field(11, props)


def char_style(bold=None, italic=None) -> bytes:
    props = (field(1, bold) if bold is not None else b"") + (field(2, italic) if italic is not None else b"")
    return field(1, b"") + field(11, props)


def table(entries: list[tuple[int, int | None]]) -> bytes:
    return b"".join(field(1, field(1, index) + (field(2, ref(r)) if r else b"")) for index, r in entries)


def pages_file(text: str, para_entries, char_entries=(), styles=()) -> bytes:
    """styles : (identifiant, 2022 ou 2021, contenu) ; entries : (position UTF-16, identifiant du style)."""
    storage = field(1, 0) + field(3, text) + field(5, table(para_entries)) + field(8, table(list(char_entries)))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Index/Document.iwa", iwa([(1, 2001, storage)]))
        archive.writestr("Index/DocumentStylesheet.iwa", iwa(list(styles)))
    return buffer.getvalue()
