<p align="center"><img src="app/static/brand/icon-192.png" width="96" alt="Pirouette"></p>

# Pirouette

Dépose tes cours (PDF, Word, PowerPoint, Pages, Keynote, texte, Markdown) et obtiens un quiz interactif pour réviser.

L'app utilise pour l'instant **l'IA locale** (Ollama) uniquement. Le moteur Claude est présent dans le code mais
masqué ; il se réactive en lançant l'app avec `PIROUETTE_CLAUDE=1`. Comparaison des deux moteurs :

| | **Local (Ollama)** | **Claude (API)** |
|---|---|---|
| Où tourne le modèle | Sur ton Mac (GPU Apple Silicon via Metal) | Chez Anthropic |
| Internet | Pas nécessaire | Nécessaire |
| Coût | Gratuit | Payant à l'usage (clé API) |
| Qualité | Bonne avec un modèle 7-14B | Excellente |
| Longs cours | Découpés en parties, questions réparties | Lus en entier en un seul appel |

## Installer l'app sur Mac (.dmg)

1. Télécharge le `.dmg` sur la page **[Releases](https://github.com/boopbipa/Pirouette/releases/latest)** :
   [Pirouette-Apple-Silicon.dmg](https://github.com/boopbipa/Pirouette/releases/latest/download/Pirouette-Apple-Silicon.dmg)
   (Mac M1, M2, M3…) ou
   [Pirouette-Intel.dmg](https://github.com/boopbipa/Pirouette/releases/latest/download/Pirouette-Intel.dmg) (Mac plus anciens).
   Ces liens pointent toujours vers la dernière version.
2. Ouvre le `.dmg` et glisse **Pirouette** dans **Applications**.
3. Premier lancement : macOS bloque les apps non signées par un certificat Apple payant.
   Réglages Système → Confidentialité et sécurité → **Ouvrir quand même** (une seule fois).
   Si macOS dit que l'app « est endommagée » : `xattr -dr com.apple.quarantine /Applications/Pirouette.app`.
4. Dans Pirouette → **Réglages** : ton prénom, l'**assistant IA locale** (installe Ollama et télécharge le modèle
   adapté à la mémoire du Mac, sans Terminal) ou ta clé Claude, et **Récupérer mes anciens cours**
   (choisis le dossier `data` de la version lancée avec `run.sh`).

Les données de l'app sont dans `~/Bibliothèque/Application Support/Pirouette`.
Ollama est une app à part : l'assistant des Réglages guide son installation.

Fabriquer le `.dmg` soi-même sur un Mac : `pip install -r requirements-desktop.txt && packaging/build_macos.sh`.

## Installation pour développer (run.sh)

Prérequis : Python 3.10+ (`brew install python` si besoin).

```bash
git clone <ce dépôt> Pirouette && cd Pirouette
./run.sh            # crée l'environnement, installe tout et ouvre http://localhost:8000
```

Sans Terminal : double-clique sur **`Pirouette.command`** dans le Finder (la première fois, macOS peut demander
une confirmation : clic droit → Ouvrir). **Laisse la fenêtre du Terminal ouverte** tant que tu utilises l'app :
si tu la fermes, `http://localhost:8000` affiche « Ce site est inaccessible ».

`./run.sh local` ou `./run.sh claude` choisit le moteur présélectionné dans l'interface.

**L'avoir dans le Dock, dans sa propre fenêtre** (sans barre d'adresse) : l'app lancée, ouvre http://localhost:8000
dans **Safari** → menu Fichier → **Ajouter au Dock** (ou, dans Chrome / Edge : menu ⋮ → Caster, enregistrer et partager →
**Installer la page en tant qu'application**). L'icône Pirouette apparaît dans le Dock ; le lanceur doit tourner en arrière-plan.

### Version locale : Ollama

1. Installe Ollama : `brew install ollama` (ou l'app depuis https://ollama.com) puis lance-le.
2. Télécharge un modèle :

   ```bash
   ollama pull qwen3.5:9b      # conseillé avec 16 à 24 Go de RAM (MacBook Pro M3 Pro 18 Go), ~6,6 Go
   ollama pull qwen3.5:27b     # meilleure qualité, 32 Go de RAM ou plus, ~17 Go
   ```

   Tous les modèles installés apparaissent dans la liste « Modèle local » de l'interface.

### Version API : Claude

1. Crée une clé API sur https://console.anthropic.com.
2. Mets-la dans le fichier `.env` (créé automatiquement au premier lancement à partir de `.env.example`) :

   ```
   ANTHROPIC_API_KEY=sk-ant-...
   ```

3. Relance `./run.sh`. Le modèle utilisé est `claude-opus-5` (modifiable via `CLAUDE_MODEL`).

## Utilisation

1. **Mes cours** : « + Nouveau cours » ouvre une petite fenêtre : glisse ton fichier (le nom du cours est repris
   du fichier), choisis éventuellement un dossier, et c'est créé (ex. « Biologie — chapitre 4 »). La page du cours a trois entrées :
   **Quiz**, **Flashcards** et **Fichiers**, chacune avec un bouton **+** pour en ajouter.
2. **Tout préparer** : après le dépôt d'un cours (et le repérage des chapitres), Pirouette propose de créer d'un coup
   un quiz et des flashcards par chapitre (10 questions et 10 cartes par défaut), en arrière-plan, chapitre après
   chapitre ; « Plus tard » pour le faire soi-même. Pendant la création, le Mac peut chauffer un peu : mieux vaut
   éviter les autres apps gourmandes.
   **Fichiers** : dépose les fichiers du cours. Quand tu avances, redépose le fichier **sous le même nom** :
   la nouvelle version remplace l'ancienne (les quiz déjà créés sont conservés et marqués « cours mis à jour depuis »).
   Pirouette découpe chaque fichier en chapitres (titres « Chapitre 2 », « II. », titres Word…), puis l'IA affine
   le découpage (« Repérer avec l'IA » / « Relancer l'IA »). Les boutons « Quiz » et « Flashcards » d'un fichier
   créent directement sur ce fichier.
3. **Créer** (bouton +) : coche les chapitres à réviser (« Sur quoi ? »), règle le quiz (nombre de questions,
   difficulté, types, part de **questions de cours** : définitions, « de quoi est composé… », termes à retrouver —
   au moins 40 %, 70 % ou 100 %) ou le nombre de cartes, puis lance. Pirouette vérifie la part de questions de cours
   et redemande précisément celles qui manquent.
   **Un quiz par chapitre** (le choix par défaut quand plusieurs chapitres sont cochés) : Pirouette crée un quiz pour
   chaque chapitre, titré avec le nom du chapitre. Les quiz se créent **en arrière-plan**, l'un après l'autre : on
   peut faire un autre quiz ou réviser en attendant ; un petit suivi en bas à gauche, visible partout dans l'app,
   montre l'avancée et propose « Commencer » dès qu'un quiz est prêt.
   **Cibler** (facultatif) : un **thème précis** (« les systèmes nerveux et leurs fonctions ») — Pirouette ne garde
   que les passages du cours qui en parlent et l'IA ne pose de questions (ou ne fait de cartes) que là-dessus ;
   **tes questions**, une par ligne — l'IA cherche la réponse dans le cours et écrit les propositions (une question
   dont la réponse n'est pas dans le cours est écartée et signalée au début du quiz). L'IA complète jusqu'au nombre
   de questions choisi. Nouveau type **Texte à trous** : une phrase du cours avec un mot essentiel à retrouver
   (réponse tapée dans la phrase, petites fautes de frappe tolérées).
4. **Quiz** : correction, explication et **mots-clés définis** après chaque réponse (la bonne réponse est surlignée).
   « Question précédente » pour revoir une question déjà faite (avec sa correction). Signaler une erreur ou supprimer
   une question hors sujet : la petite bulle à droite des boutons.
   À la fin : score, correction (tout ou mes erreurs seulement), « Refaire mes erreurs » ou « Refaire le quiz ».
   Un quiz se renomme en entier (y compris « Quiz 3 · ») avec le crayon dans la liste. Une question hors sujet se
   supprime (« Supprimer cette question », pendant le quiz, dans la correction ou en révision).
   Une question fausse ? « Signaler » (sous la question ou dans la correction) : la question, les choix et la bonne
   réponse sont joints, on ajoute un message, et Pirouette ouvre l'app Mail avec le signalement prêt à envoyer
   (une copie reste dans `data/feedback.json`).
   Un nouveau quiz ne repose pas les questions des quiz déjà créés sur le même cours, même reformulées.
5. **Flashcards** : toutes les cartes sont affichées ; **passe la souris** sur une carte pour voir la réponse,
   **clique** dessus quand tu la connais (elle passe dans « Apprises », reclique pour la remettre « À revoir »).
   « Réviser une par une » propose les cartes du filtre choisi, une à une (Espace pour retourner, ← / →).
   Ajouter des cartes complète le paquet sans doublon. Au survol d'une carte : ✎ pour la corriger, ✕ pour la
   supprimer (✎ aussi pendant la révision une par une). « ✎ Écrire une carte » en ajoute une à la main
   (ou via le « + » de la tuile Flashcards → « Écrire une carte » ; « Ajouter et en écrire une autre » pour en enchaîner plusieurs ; Pirouette prévient si elle ressemble à une carte existante).
6. **Dossiers** : « + Dossier » sur l'accueil (un semestre, une UE…). Glisse un cours sur un dossier pour l'y ranger,
   ou choisis son dossier en haut de la page du cours. Un dossier se replie, se renomme (✎) et s'**archive** à la fin
   du semestre : il passe dans « Archivés », replié, en bas de l'accueil. Supprimer un dossier garde ses cours.

Les fichiers **Pages** et **Keynote** sont lus directement par Pirouette (format actuel et ancien format '09), sans
avoir besoin de ces apps. En dernier recours seulement, Pirouette demande à Pages / Keynote de convertir le fichier.

Les PDF scannés (images sans texte) ne sont pas lus : passe-les d'abord dans un OCR (par ex. « Aperçu » > Exporter en PDF avec texte, ou `ocrmypdf`).

**Tes définitions** : à l'import, le gras et l'italique sont gardés (Pages et Word ; les fichiers déjà importés sont
relus automatiquement). Réglages → Tes définitions : importe un bout de cours ou colle un passage, Pirouette montre ce
qu'elle repère (par exemple « un titre seul sur sa ligne, en gras et italique, puis sa définition ») et tu valides.
Chaque fichier affiche ensuite « N définitions repérées » (cliquable) ; ces définitions servent en priorité aux
questions de cours et aux flashcards.

**Uniquement ton cours** : pour chaque question et chaque carte, l'IA doit recopier la phrase du cours qui contient
la réponse. Pirouette vérifie que cette phrase existe bien dans les chapitres choisis, et que les mots importants de la
réponse s'y trouvent ; sinon la question est écartée et remplacée (le modèle ne peut pas compléter avec ce qu'il sait
par ailleurs). La phrase s'affiche ensuite sous l'explication (« Dans ton cours »). Les marques de gras et
d'italique du cours (« ***GABA*** ») sont retirées des questions, des cartes et des citations.

**Mémoire de lecture (contexte)** : Réglages → IA locale → étape 3. Pirouette la règle d'office selon la mémoire du
Mac (32 768 tokens avec 18 Go, soit environ 27 pages de cours lues d'un coup) et l'envoie à chaque demande : pas de
Modelfile ni de Terminal. On peut la baisser (Mac qui peine) ou la monter si la mémoire le permet.

**Réflexion des modèles** : les modèles qui « réfléchissent » avant de répondre (qwen3, deepseek-r1…) le font
sans que ça se voie, ce qui rallonge beaucoup la génération et fait chauffer le Mac. Pirouette coupe cette
réflexion par défaut ; Réglages → IA locale → « Laisser le modèle réfléchir avant de répondre » la réactive.

**Menu du haut** : **Accueil** (message de bienvenue, cartes du jour et un seul bouton « Réviser »), **Mes cours** (cours et dossiers),
**Réviser**, Réglages et **Feedback** (une idée, un bug : Pirouette prépare le mail).
La page d'un cours ne montre que ses trois entrées (Quiz, Flashcards, Fichiers) ; chacune ouvre sa propre page.
Un **fil d'Ariane** en haut (Mes cours › Cours › Quiz › …) permet de remonter d'un clic. Les menus déroulants
sont aux couleurs de l'app.

**Réviser** (répétition espacée, comme Anki) : d'abord on choisit quoi réviser (tous ses cours, un dossier ou un
cours), puis on arrive sur la révision du jour, les points faibles et le suivi (replié, « Détails » pour tout voir).
- **Révision du jour** : les cartes dont le rappel est arrivé, plus 20 nouvelles cartes au plus par jour, avec des
  questions de tes quiz glissées entre les cartes (celles ratées, jamais faites ou réussies il y a plus d'une semaine).
  Après chaque carte : **Je ne savais pas / À moitié / Je savais** (touches 1 à 3). Pirouette en déduit quand la
  reposer (plus tôt si on ne savait pas, de plus en plus tard si on savait), sans l'afficher. Un clic sur la carte la
  retourne, un autre revient à la question. À la fin : « Voir la correction » (tout, ou mes erreurs seulement) et
  « Refaire mes erreurs ».
- **J'écris la réponse** (au lieu de retourner la carte) : Pirouette compare ta réponse au verso, surligne les mots
  retrouvés et propose une réponse (« C'est juste », « Presque », « Pas tout à fait ») ; c'est toi qui décides.
- **Mode partiel** (sur un cours) : 20 questions de quiz (réparties entre tous les quiz du cours) + 10 flashcards par
  défaut, modifiables ; chronomètre au choix (aucun par défaut, chrono qui défile ou compte à rebours). Conditions
  d'examen : rien n'est corrigé avant « Rendre ma copie » ; on passe les questions, on marque « Je doute », on revient
  sur n'importe laquelle. Les flashcards se répondent par écrit ; les réponses écrites se corrigent soi-même ou par
  l'IA (elle juge l'idée, pas la formulation, et chaque verdict se conteste). Note sur 20, correction (tout / mes
  erreurs), « Refaire mes erreurs », et l'historique des dernières notes.
- **Mes points faibles** (dans le Suivi) : les cartes souvent oubliées et les questions de quiz ratées la dernière fois.
- **Suivi** : cartes révisées et réussite des 7 derniers jours, calendrier des 12 dernières semaines, cartes à revoir
  les 7 prochains jours, bilan par cours. On peut réviser tous ses cours (hors dossiers archivés), un dossier ou un cours.
- Sur la page d'un cours : « Réviser ce cours » et « Mes points faibles ». Dans la grille des flashcards, le verso
  d'une carte apprise indique son prochain rappel.
- **Rappel quotidien** (app Mac) : Réglages → Révisions → « Me rappeler de réviser chaque jour » et l'heure. Une
  notification arrive à l'heure choisie, même app fermée, s'il y a des cartes du jour (via un fichier de lancement
  dans `~/Library/LaunchAgents`, supprimé quand on décoche). Le même réglage fixe le nombre de nouvelles cartes par jour.

**Explique-moi** : dans la correction d'un quiz, d'une séance de révision ou d'un partiel, « Explique-moi » demande à
l'IA de réexpliquer la notion à partir du passage du cours d'où vient la question (et de ce qui n'allait pas dans ta
réponse), avec une astuce pour retenir.

**Cours mis à jour** : quand tu redéposes une nouvelle version d'un fichier, Pirouette repère les questions et les
cartes dont la phrase du cours a disparu (« Ton cours a changé : … ne correspondent plus au cours ») : on coche celles
à supprimer, les autres sont gardées et ne sont plus signalées.

**Semaine des partiels** : sur un dossier (icône calendrier, pour tous ses cours) ou sur un cours (menu « ••• »), le
premier jour de la semaine d'examens. L'accueil et Réviser affichent « Partiels dans N jours » ; les rappels des cartes
tombent toujours avant la semaine, se resserrent les deux dernières semaines, puis reviennent chaque jour pendant la
semaine ; les nouvelles cartes sont étalées pour être toutes vues à temps.

**Mises à jour automatiques** (app Mac) : au lancement, Pirouette vérifie s'il existe une version plus récente et
propose « Mettre à jour » : elle télécharge le .dmg de ce Mac et prépare la nouvelle version, puis t'invite à quitter
Pirouette et à la rouvrir ; l'app est remplacée pendant qu'elle est fermée (les données ne bougent pas). Réglages → Version → « Vérifier les mises à jour », ou le menu **Mise à jour** de la barre des menus du Mac (version actuelle, « Rechercher une mise à jour… », « Installer la mise à jour »). Il faut que les versions soient publiées sur
un dépôt GitHub public (`PIROUETTE_UPDATE_REPO` pour en changer).

**Couleur de l'app** : terre cuite par défaut (fond brun en mode sombre) ; Réglages → Couleur de l'app pour en changer
(roue chromatique, couleurs toutes prêtes, aperçu clair et sombre).

Toutes les données restent sur ton Mac, dans le dossier `data/` (cours, fichiers originaux, quiz, cartes,
prénom). Les compteurs (cartes à revoir, suivi des révisions) sont calculés sur place : rien n'est envoyé ailleurs.

## Configuration (`.env`)

| Variable | Défaut | Rôle |
|---|---|---|
| `QUIZZ_DEFAULT_PROVIDER` | `local` | Moteur présélectionné (`local` ou `claude`) |
| `OLLAMA_URL` | `http://localhost:11434` | Adresse d'Ollama |
| `OLLAMA_MODEL` | selon la mémoire (`qwen3.5:9b` avec 18 Go) | Modèle local présélectionné |
| `OLLAMA_NUM_CTX` | selon la mémoire (32 768 avec 18 Go) | Fenêtre de contexte (sinon réglée dans Réglages) |
| `OLLAMA_CHUNK_CHARS` | 1,5 × le contexte | Taille des parties de cours envoyées au modèle local |
| `ANTHROPIC_API_KEY` | — | Clé API Claude |
| `CLAUDE_MODEL` | `claude-opus-5` | Modèle Claude |

## Structure

```
app/
  main.py                  serveur FastAPI (cours, chapitres, génération en streaming, quiz, flashcards)
  storage.py               stockage des cours, fichiers, quiz et flashcards dans data/
  chapters.py              découpage en chapitres (repérage automatique, puis affiné par l'IA)
  definitions.py           repère les définitions d'après la mise en forme (règle choisie dans Réglages)
  grounding.py             vérifie que chaque question / carte vient du cours (phrase citée, mots de la réponse)
  extract.py               extraction du texte : PDF, DOCX, PPTX, Pages, Keynote, TXT, MD
  iwork.py                 lecture directe des fichiers Pages / Keynote (archives IWA : Snappy + Protobuf)
  quiz.py                  schémas JSON, prompts, découpage du cours, validation des questions
  revision.py              flashcards : schéma, prompt, validation, doublons
  local_ai.py              assistant IA locale : état d'Ollama, modèle conseillé selon la mémoire, téléchargement
  providers/
    ollama_provider.py     version locale
    claude_provider.py     version API Claude
  static/                  interface web (HTML/CSS/JS, sans build)
    brand/                 logo (silhouette du chat) et icônes de l'app
    fonts/                 police Bricolage Grotesque (SIL OFL), intégrée pour le hors-ligne
desktop.py                 app de bureau : serveur en arrière-plan + fenêtre (pywebview)
packaging/                 fabrication de Pirouette.app et du .dmg (PyInstaller, icône, test de fumée)
.github/workflows/         fabrication automatique des .dmg sur des Mac GitHub + page Releases
tests/                     tests pytest
docs/                      maquettes : pistes d'identité (logo, couleurs, polices)
```

Les deux moteurs utilisent le même schéma JSON (sorties structurées côté Claude, paramètre `format` côté Ollama) ; chaque question est ensuite vérifiée (bonne réponse présente parmi les choix, pas de doublon…) avant d'être affichée.

## Dépannage

| Symptôme | Solution |
|---|---|
| « Ce site est inaccessible » / `ERR_CONNECTION_REFUSED` | L'app n'est pas lancée : lance `./run.sh` (ou `Pirouette.command`), attends « Pirouette démarre », et garde la fenêtre ouverte. |
| « Il faut Python 3.10 ou plus récent » | `brew install python@3.12` puis relance. |
| `permission denied: ./run.sh` | `chmod +x run.sh Pirouette.command` |
| « Le port 8000 est déjà utilisé » | L'app tourne déjà : ouvre simplement http://localhost:8000. |
| « Local (Ollama) » grisé | Ouvre l'app Ollama et vérifie qu'un modèle est installé (`ollama list`). |

## Tests

```bash
.venv/bin/pip install pytest
.venv/bin/python -m pytest
```
