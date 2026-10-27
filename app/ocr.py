"""Lecture des PDF scannés (pages en images) par la reconnaissance de texte de l'ordinateur : Vision sur Mac,
Windows.Media.Ocr sur Windows. Rien à télécharger : c'est celle du système (photos, captures…)."""

from __future__ import annotations

import asyncio
import io
import sys

SCALE = 2.5        # rendu des pages ≈ 180 dpi : assez net pour l'OCR, sans être trop lourd
MAX_SIDE = 4000    # côté le plus long de l'image envoyée (Windows refuse au-delà de ~10 000 px)


class OcrUnavailable(Exception):
    """Pas de reconnaissance de texte sur cet ordinateur (Linux, Windows sans langue OCR…)."""


def status() -> dict:
    """Disponible ici ? Sinon pourquoi (vérifié par le test de l'app empaquetée)."""
    try:
        _engine()
    except OcrUnavailable as exc:
        return {"available": False, "reason": str(exc)}
    return {"available": True, "reason": ""}


def ocr_pdf(data: bytes, on_page=None) -> str:
    """Texte d'un PDF scanné, page par page (« [Page n] »). `on_page(page, pages)` suit l'avancée."""
    import pypdfium2 as pdfium

    engine = _engine()
    pdf = pdfium.PdfDocument(data)
    try:
        total = len(pdf)
        pages = []
        for i in range(total):
            page = pdf[i]
            width, height = page.get_size()
            scale = min(SCALE, MAX_SIDE / max(width, height, 1))
            image = page.render(scale=scale).to_pil().convert("RGB")
            buffer = io.BytesIO()
            image.save(buffer, "PNG")
            content = engine(buffer.getvalue()).strip()
            if content:
                pages.append(f"[Page {i + 1}]\n{content}")
            if on_page:
                on_page(i + 1, total)
        return "\n\n".join(pages)
    finally:
        pdf.close()


def _engine():
    """Fonction image PNG → texte, selon le système."""
    if sys.platform == "darwin":
        return _mac_engine()
    if sys.platform == "win32":
        return _windows_engine()
    raise OcrUnavailable("Reconnaissance de texte disponible sur Mac et Windows seulement.")


def _mac_engine():
    try:
        import objc
        import Vision
        from Foundation import NSData
    except ImportError as exc:
        raise OcrUnavailable(str(exc)) from exc

    def recognize(png: bytes) -> str:
        # Une page = des centaines d'objets Objective-C : on les libère à chaque page (un manuel en a 400).
        with objc.autorelease_pool():
            handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(
                NSData.dataWithBytes_length_(png, len(png)), None)
            request = Vision.VNRecognizeTextRequest.alloc().init()
            request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
            request.setUsesLanguageCorrection_(True)
            request.setRecognitionLanguages_(["fr-FR", "en-US"])
            ok, error = handler.performRequests_error_([request], None)
            if not ok:
                raise RuntimeError(str(error))
            lines = []
            for observation in request.results() or []:
                candidates = observation.topCandidates_(1)
                if candidates:
                    lines.append(str(candidates[0].string()))
            return "\n".join(lines)

    return recognize


def _windows_engine():
    try:
        from winrt.windows.globalization import Language
        from winrt.windows.graphics.imaging import BitmapDecoder
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream
    except ImportError as exc:
        raise OcrUnavailable(str(exc)) from exc
    # Le français si Windows l'a, sinon les langues de l'utilisateur, sinon la première installée.
    french = Language("fr-FR")
    engine = (OcrEngine.try_create_from_language(french) if OcrEngine.is_language_supported(french) else None) \
        or OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        languages = list(OcrEngine.available_recognizer_languages)
        engine = OcrEngine.try_create_from_language(languages[0]) if languages else None
    if engine is None:
        raise OcrUnavailable("Aucune langue de reconnaissance de texte installée dans Windows.")

    async def read(png: bytes) -> str:
        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream.get_output_stream_at(0))
        writer.write_bytes(png)
        await writer.store_async()
        await writer.flush_async()
        writer.detach_stream()
        stream.seek(0)
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        result = await engine.recognize_async(bitmap)
        return "\n".join(line.text for line in result.lines)

    return lambda png: asyncio.run(read(png))
