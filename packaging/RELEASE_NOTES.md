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
3. Dans Pirouette → **Réglages**, branche l'**app Claude** : c'est elle qui crée tes quiz et tes flashcards.

## Télécharger Pirouette pour Windows (bêta)

**Pirouette-Windows-Setup.exe** (Windows 10 ou 11, 64 bits)

1. Lance le fichier téléchargé. Windows peut afficher « Windows a protégé votre ordinateur » (l'app n'a pas de
   certificat payant) : clique sur **Informations complémentaires**, puis **Exécuter quand même**. Une seule fois.
2. L'installation se fait pour toi seul, sans mot de passe administrateur. Pirouette apparaît dans le menu Démarrer.
3. Les mises à jour arrivent comme sur Mac : un clic, puis ferme et rouvre Pirouette.

**L'installateur est bloqué ?** (ordinateur d'établissement : « Impossible d'exécuter un fichier depuis le dossier
temporaire ») Prends **Pirouette-Windows-Portable.zip** : clic droit → **Extraire tout…** (par exemple dans Documents),
puis lance **Pirouette.exe** dans le dossier extrait. Rien à installer ; pour une nouvelle version, refais la même chose.

Sous Windows : pas de rappel quotidien pour l'instant, et les fichiers Pages / Keynote ne sont lus que s'ils
contiennent un aperçu (sinon, exporte-les en PDF depuis un Mac).

