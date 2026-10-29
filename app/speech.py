"""La voix de l'ordinateur, pour lire les cartes quand la page n'a pas accès aux voix du navigateur."""

from __future__ import annotations

import subprocess
import sys


def start(text: str, rate: float = 1.0) -> subprocess.Popen | None:
    """Lance la lecture et rend la main tout de suite (le processus, pour pouvoir l'arrêter). None : pas de voix ici."""
    text = text.strip()
    if not text:
        return None
    rate = min(max(rate, 0.5), 2.0)
    if sys.platform == "darwin":
        # say : 175 mots par minute environ par défaut ; la voix française du système si elle existe
        command = ["say", "-r", str(round(175 * rate))]
        voice = _mac_french_voice()
        if voice:
            command += ["-v", voice]
        return subprocess.Popen([*command, "--", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if sys.platform == "win32":
        # System.Speech : vitesse de -10 à 10 ; le texte passe par l'entrée standard (pas d'échappement à faire)
        speed = round((rate - 1) * 6)
        script = ("[Console]::InputEncoding = [Text.Encoding]::UTF8; Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                  "$v = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like 'fr*' } | Select-Object -First 1; "
                  "if ($v) { $s.SelectVoice($v.VoiceInfo.Name) }; "
                  f"$s.Rate = {speed}; $s.Speak([Console]::In.ReadToEnd())")
        proc = subprocess.Popen(["powershell", "-NoProfile", "-Command", script], stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
        proc.stdin.write(text.encode("utf-8"))
        proc.stdin.close()
        return proc
    return None


_french: list[str | None] = []


def _mac_french_voice() -> str | None:
    """La première voix française installée (« Thomas », « Amélie »…), cherchée une seule fois."""
    if not _french:
        try:
            listing = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            listing = ""
        voices = [line.split("  ")[0].strip() for line in listing.splitlines() if " fr_FR " in line or " fr_CA " in line]
        enhanced = [v for v in voices if "(" in v]  # « Thomas (amélioré) » : la version téléchargée, plus naturelle
        _french.append((enhanced or voices or [None])[0])
    return _french[0]
