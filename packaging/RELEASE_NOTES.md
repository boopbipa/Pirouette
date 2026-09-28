## Télécharger Pirouette pour Mac

| Ton Mac | Fichier |
|---|---|
| Puce Apple (M1, M2, M3, M4…) | **Pirouette-Apple-Silicon.dmg** |
| Processeur Intel (Mac plus anciens) | **Pirouette-Intel.dmg** |

Pour savoir lequel prendre : menu  → **À propos de ce Mac** → ligne « Puce » (Apple) ou « Processeur » (Intel).

### Installer
1. Ouvre le `.dmg` et glisse **Pirouette** dans **Applications**.
2. Premier lancement : macOS bloque l'app (elle n'a pas de certificat Apple payant).
   Clique sur OK, puis **Réglages Système → Confidentialité et sécurité → Ouvrir quand même**. Une seule fois.
   Si macOS dit que l'app « est endommagée » : dans le Terminal, `xattr -dr com.apple.quarantine /Applications/Pirouette.app`
3. Dans Pirouette → **Réglages**, choisis ton IA :
   - **IA locale** (gratuite, hors ligne) : l'assistant te guide pour installer Ollama et télécharger le bon modèle pour ton Mac, sans Terminal.
   - **Claude** : colle une clé API.
