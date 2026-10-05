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

## Télécharger Pirouette pour Windows (bêta)

**Pirouette-Windows-Setup.exe** (Windows 10 ou 11, 64 bits)

1. Lance le fichier téléchargé. Windows peut afficher « Windows a protégé votre ordinateur » (l'app n'a pas de
   certificat payant) : clique sur **Informations complémentaires**, puis **Exécuter quand même**. Une seule fois.
2. L'installation se fait pour toi seul, sans mot de passe administrateur. Pirouette apparaît dans le menu Démarrer.
3. Les mises à jour arrivent comme sur Mac : un clic, puis ferme et rouvre Pirouette.

Sous Windows : pas de rappel quotidien pour l'instant, et les fichiers Pages / Keynote ne sont lus que s'ils
contiennent un aperçu (sinon, exporte-les en PDF depuis un Mac).

