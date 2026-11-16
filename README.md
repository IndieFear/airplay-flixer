<p align="center"><img src="extension/icons/app.svg" width="112" alt="Icône Airplay Flixer"></p>

# Airplay Flixer

Un compagnon dans la barre de menus du Mac et une extension Chrome pour diffuser des vidéos web sur Apple TV, avec sous-titres, télécommande et téléchargement.

**[Télécharger la dernière version](https://github.com/IndieFear/airplay-flixer/releases/latest)** · [Signaler un problème](https://github.com/IndieFear/airplay-flixer/issues)

## Installation depuis une version publiée

Le ZIP contient l’application Mac, le dossier de l’extension et un guide **Commencer ici.html**.

1. Télécharge puis décompresse le ZIP depuis les versions publiées.
2. Glisse **Airplay Flixer.app** dans **Applications**, puis ouvre l’application.
3. Conserve le dossier **Chrome Extension** sur ton Mac. Dans `chrome://extensions`, active **Mode développeur**, puis **Charger l’extension non empaquetée** et choisis ce dossier.
4. Dans l’extension, ouvre **Paramètres**, recherche ton Apple TV, enregistre la destination et lance l’appairage.
5. Recharge la page vidéo, lance la vidéo puis utilise l’extension pour diffuser.

La version précompilée cible **Apple Silicon et macOS 26 ou ultérieur**. Elle est signée localement et nécessite une confirmation macOS au premier lancement. Les fonctions HLS avancées et la synchronisation des sous-titres nécessitent FFmpeg, disponible avec `brew install ffmpeg`.

Les clés OpenSubtitles et SubDL se renseignent dans l’extension sur chaque Mac. Chacun associe ses propres appareils.


Une extension Chrome et un compagnon macOS discret pour envoyer une vidéo web à une Apple TV via **pyatv**. Le compagnon possède uniquement une icône AirPlay dans la barre de menus : pas de fenêtre principale, de Dock, d’Electron, de serveur distant ni de compte.

## Installation depuis les sources

Construis d’abord l’application avec les commandes de la section **Construire et vérifier**.

1. Lance **Installer Airplay Flixer.command**. L’application est copiée dans `~/Applications` et lancée. Elle enregistre son pont Native Messaging pour Chrome.
2. Dans l’icône AirPlay de la barre de menus, ouvre **Choisir une Apple TV → Rechercher les Apple TV…**, choisis la tienne, puis **Associer l’Apple TV…**. Entre le code affiché à la télévision. Si macOS le demande, autorise le réseau local pour Airplay Flixer.
3. Ouvre `chrome://extensions`, active **Mode développeur**, puis **Charger l’extension non empaquetée**. Choisis le dossier **dist/Chrome Extension** de ce projet. Tu peux aussi passer par **Installer l’extension Chrome…** dans le menu du compagnon.
4. Épingle l’extension dans Chrome. **Recharge la page vidéo après l’installation**, lance la vidéo, ouvre l’extension puis clique **Caster sur Apple TV**.

Le raccourci **⌥ ⇧ A** lance directement la meilleure source détectée vers l’Apple TV favorite. Il privilégie la piste de sous-titres active du lecteur, puis une piste française détectée. Les pistes intégrées au HLS sont conservées si aucune piste externe n’est choisie.

Le Mac doit rester allumé sur le même réseau que l’Apple TV. Quitter le compagnon arrête la diffusion. Pour démarrer à la connexion, ajoute l’application dans les éléments d’ouverture de session des réglages macOS.

## Vidéo et sous-titres

- **HLS (`.m3u8`)** : relais des manifestes, variantes, audio, segments, clés AES-128 ordinaires et sous-titres intégrés. Le média n’est pas téléchargé en entier et la vidéo n’est pas réencodée.
- **MP4/M4V/MOV sans sous-titres externes** : relais HTTP avec prise en charge des requêtes Range, pour conserver l’avance et le retour dans le média.
- **SRT et WebVTT externes** : conversion en WebVTT, synchronisation sur l’horodatage réel du média et déclaration dans le manifeste HLS. **FFmpeg/ffprobe est nécessaire** pour cette synchronisation. Il est déjà présent sur le Mac de développement ; sur un autre Mac : `brew install ffmpeg`.
- **MP4 avec sous-titres externes, MKV/WebM non protégés** : FFmpeg adapte le conteneur en HLS, copie la vidéo et encode l’audio en AAC. Ce mode utilise un cache temporaire plafonné à 6 Go, supprimé à l’arrêt. L’avance est limitée à la portion déjà préparée. Un codec vidéo incompatible avec l’Apple TV reste incompatible : il n’y a pas de réencodage vidéo automatique. Les flux DASH sont détectés mais ne sont pas pris en charge dans cette version.
- **Flux HLS avec ruptures d’horodatage** : sous-titres intégrés possibles ; ajout de sous-titres externes refusé pour éviter une désynchronisation silencieuse.
- **DRM** : pas de déchiffrement ou de contournement. Les flux FairPlay/SAMPLE-AES détectés sont refusés.

La position du lecteur web est transmise à AirPlay. Chrome n’est mis en pause automatiquement que si le compagnon observe des demandes de média venant de l’Apple TV **et** un état de lecture actif retourné par pyatv. Si ce retour n’est pas disponible, Chrome reste en lecture : son bouton Pause peut être utilisé manuellement.

Les sous-titres ajoutés directement dans l’image par le JavaScript du site, sans piste SRT/VTT ou `TextTrack` accessible, nécessitent une adaptation spécifique du lecteur. Une URL `blob:` seule n’est pas transmissible : l’extension observe les véritables chargements réseau après son installation. Une source peut rester indisponible si elle dépend d’une protection ou d’une session impossible à reproduire hors du navigateur.

## Données et accès

- L’extension observe les URL/types de médias sur HTTP(S) ; les permissions sur les sites sont nécessaires pour les hébergeurs vidéo et les lecteurs intégrés sur d’autres domaines.
- Les informations de session utiles au média restent dans `chrome.storage.session` et disparaissent avec la session Chrome. Elles sont transmises au compagnon lors d’un clic Caster, jamais à un serveur tiers.
- Les cookies et en-têtes Authorization sont limités à leur origine ; ils ne suivent pas les redirections vers un autre domaine.
- Le contrôle utilise Native Messaging, réservé à l’identifiant stable de cette extension, puis une API HTTP sur **127.0.0.1**, protégée par un jeton aléatoire. Aucune commande n’est exposée au réseau local.
- Le relais média écoute sur le réseau local, avec une URL temporaire imprévisible et des accès limités à l’adresse de l’Apple TV sélectionnée et au Mac. Il disparaît lorsque l’application quitte.
- Les préférences, identifiants d’appairage pyatv et journaux se trouvent dans `~/Library/Application Support/Airplay Flixer`. Les identifiants et jetons sont enregistrés avec des permissions `0600`. Les URL vidéo, cookies et codes PIN ne sont pas journalisés.

L’application est signée localement pour ce Mac ; elle n’est pas notarialisée pour une distribution publique. La compatibilité AirPlay dépend de pyatv, du modèle d’Apple TV, de tvOS et du format du média. Un test sur l’appareil réel reste nécessaire.

## Construire et vérifier

Python 3.12+, Xcode Command Line Tools et Node.js sont requis pour le développement. Python est embarqué dans l’application construite ; l’utilisateur n’a pas besoin de l’installer.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
npm test
.venv/bin/python scripts/build.py
```

`requirements-lock.txt` contient les versions exactes utilisées pour la livraison sur ce Mac. Pour reproduire cet environnement, utilise-le à la place de `requirements-dev.txt`.

Pour lancer uniquement le moteur en développement :

```sh
PYTHONPATH=companion .venv/bin/python companion/entry.py
```

Le build crée l’app, le dossier de l’extension, leurs ZIP individuels et une archive à partager `Airplay-Flixer-<version>-Mac-<architecture>.zip` dans `dist/`. Cette dernière contient les deux composants, le guide de démarrage et les notices tierces. L’identifiant Chrome est dérivé de la clé publique versionnée dans le manifeste et reste stable après reconstruction.

## Désinstaller

Quitte le compagnon, supprime l’extension depuis Chrome et place `~/Applications/Airplay Flixer.app` dans la corbeille. Le pont Chrome est `~/Library/Application Support/Google/Chrome/NativeMessagingHosts/com.airplay_flixer.companion.json`. Le dossier `~/Library/Application Support/Airplay Flixer` contient les préférences et les appairages ; supprime-le si tu veux les oublier.

## Compatibilité tvOS 27

La version PyPI de pyatv 0.18.0 ne déclenche pas la lecture vidéo sur cette Apple TV sous tvOS 27.2. Cette livraison utilise le correctif public de Rob Kochman, figé au commit `8144c77c6cecbed4f9ba2adb5a350ad86a8f6604`, qui utilise `insertPlayQueueItem` et les événements réels de lecture. Il est référencé dans https://github.com/postlund/pyatv/pull/2846 et n’est pas encore intégré à la version publiée. Le clip H.264/AAC local a été téléchargé par l’Apple TV et son affichage a été confirmé par l’utilisateur. Aucun refus de démarrage n’est masqué.

## Mini contrôleur sur le Mac

Dans l’icône AirPlay de la barre de menus, ouvre **Mini contrôleur…**. La petite fenêtre reste au premier plan et se ferme sans quitter le compagnon. Clique dans la timeline ou déplace le curseur pour reprendre le film à cet instant ; le milieu de la barre correspond à la moitié du film. Les boutons −10 s, +10 s et Lecture/Pause contrôlent l’Apple TV.

La position est ancrée sur les événements AirPlay, puis avance entre les retours de la TV. Les sauts utilisent le format CMTime compris par tvOS 27 et relancent la même source à l’instant demandé, avec un court temps de chargement. Les sous-titres et le relais restent en place. Pour les médias en cours de conversion, une position au-delà de la partie déjà préparée est refusée explicitement.

## Contrôleur dans l’extension (0.3.0)

Le panneau Chrome contient la timeline cliquable, les temps de lecture, Lecture/Pause, les sauts de dix secondes et Arrêter. Il contrôle aussi une diffusion lancée depuis un autre onglet. Les options regroupent la destination, les sources vidéo et les sous-titres pour le prochain lancement. Le compagnon 0.2.0 reste compatible et continue de gérer la connexion AirPlay dans la barre de menus.

Pour mettre à jour une installation non empaquetée, ouvre `chrome://extensions` (ou `brave://extensions`) et utilise Recharger sur Airplay Flixer. Le dossier chargé sur ce Mac est `extension/` dans ce projet. Le rechargement du panneau conserve la session AirPlay du compagnon.

### Identification des flux (extension 0.4.0)

Les playlists HLS sont analysées à l’ouverture de l’extension : master automatique, résolution, pistes audio séparées, langues, codecs, débit et fréquence d’images lorsqu’ils sont déclarés. Le master est privilégié au choix automatique ; les variantes dont le son est séparé sont signalées. Le détail de la source sélectionnée apparaît dans les options de diffusion.

L’analyse est limitée à 12 playlists, avec un cache et des lectures bornées. Une playlist inaccessible ou sans déclaration ne permet pas de confirmer la qualité ou le son : « qualité non précisée » et « son à vérifier » restent affichés. Les fichiers directs et le DASH ne sont pas sondés. Recharge l’extension dans `brave://extensions` ou `chrome://extensions` après mise à jour ; le compagnon ne nécessite aucun redémarrage.

### Statistiques pendant la diffusion

Le contrôleur affiche la résolution déclarée de la dernière variante HLS dont les segments sont effectivement envoyés au récepteur, un débit estimé sur les derniers segments vidéo complets (audio inclus si multiplexé), et le débit de transfert du relais sur une fenêtre de dix secondes. Ces informations se rafraîchissent avec le statut chaque seconde. Le débit annoncé est utilisé comme repli explicite lorsque le débit des segments n’est pas encore mesuré. Les lectures locales de diagnostic sont exclues. Le transfert peut tomber à zéro pendant la lecture depuis le tampon ; la dernière résolution envoyée peut différer de celle affichée au même instant par le téléviseur. La résolution reste indisponible pour un flux qui ne la déclare pas, un fichier direct ou un flux remultiplexé localement.

### Recherche des appareils DLNA (extension 0.5.0 / compagnon 0.3.0)

Le bouton de recherche interroge en parallèle AirPlay et DLNA/UPnP (SSDP IPv4, MediaRenderer et AVTransport). Les récepteurs DLNA apparaissent avec leur nom dans l’extension et la barre de menus. La découverte est limitée au réseau local, sans identifiants du navigateur. Les appareils doivent être allumés, sur le même réseau, avec leur fonction de partage/réception multimédia activée ; un réseau invité ou un filtrage multicast peut empêcher la détection. Les serveurs multimédias seuls sont exclus.

Depuis la version 0.6.0 de l’extension et 0.4.0 du compagnon, les appareils DLNA sont sélectionnables et acceptent la diffusion via AVTransport. Une même TV compatible AirPlay et DLNA peut apparaître sous les deux protocoles.

### Diffusion DLNA

Choisis un appareil marqué DLNA, puis caste la source vidéo depuis l’extension. Le compagnon lui transmet une URL locale, fournit les métadonnées DIDL-Lite et assure le relais avec les accès du navigateur. Il utilise SetAVTransportURI, Play, Pause, Stop, Seek (REL_TIME), GetTransportInfo et GetPositionInfo. La timeline suit l’état du récepteur ; les déplacements n’ouvrent pas une nouvelle vidéo et sont désactivés si la TV indique que Seek n’est pas pris en charge. La reprise à la position du navigateur est tentée après démarrage du lecteur.

La prise en charge des formats dépend du téléviseur : MP4 est un bon premier essai, HLS/MKV/WebM dépendent du lecteur et des codecs. Pour les TV qui refusent HLS ou ne l’annoncent pas, le compagnon utilise FFmpeg : variante H.264 jusqu’à 1080p, AAC stéréo et flux MPEG-TS unique (M2TS pour les récepteurs qui l’exigent). La vidéo est copiée sans réencodage si elle est déjà compatible ; sinon elle est convertie en H.264 8 bits jusqu’à 1080p. FFmpeg/ffprobe doivent être installés. DASH n’est pas pris en charge. Les pistes de sous-titres externes ne sont pas transmises ; l’extension indique d’utiliser les pistes intégrées depuis le téléviseur, qui décide de leur activation. La confirmation de lecture provient du statut AVTransport, et non du seul succès de la commande Play. Les équipements qui ne répondent plus produisent un message d’erreur.

### Repli HLS → MPEG-TS en DLNA (compagnon 0.4.1)

Le compagnon consulte GetProtocolInfo du ConnectionManager avant le chargement. En l’absence de HLS annoncé, ou après refus de format AVTransport 704/714, il prépare un flux compatible. Le master est réduit à une seule variante vidéo avec son groupe audio correspondant ; cela évite d’envoyer une variante silencieuse. Le flux HTTP est produit progressivement, sans annoncer une taille de fichier finale fictive, puis prend en charge les requêtes de plage une fois complet. Les accès au CDN restent dans le relais local.

La préparation utilise un cache temporaire plafonné à 6 Go, supprimé à l’arrêt. La diffusion démarre à la position du navigateur quand la source est seekable, mais la timeline ne permet pas de déplacement pendant ce mode à la volée. La pause et l’arrêt restent disponibles. Les codecs et la résolution effectifs sont sondés avant l’envoi. Les sources HEVC/HDR peuvent nécessiter une conversion et ne conservent pas nécessairement le HDR.

### Interface de l’extension 0.7.0

L’onglet Lecture regroupe le mini-contrôleur, les sources vidéo toujours visibles et les sous-titres. Le choix des sous-titres commence par la langue, puis affiche uniquement les pistes correspondantes ; français et anglais sont placés en premier. Les langues sont déterminées à partir des métadonnées ou d’indications reconnaissables dans les noms. Les pistes sans indication restent dans « Langue non précisée ». La sélection conserve l’index original attendu par le compagnon, et les noms complets restent accessibles au survol.

L’onglet Paramètres regroupe la recherche d’appareils, la destination persistante et l’appairage Apple TV. Le bouton « Utiliser par défaut » enregistre explicitement le choix dans le compagnon existant ; choisir une ligne dans la liste ne change pas immédiatement de récepteur. Enregistrer une autre destination pendant une lecture arrête la diffusion, comme indiqué dans l’interface. Aucun redémarrage du compagnon n’est nécessaire pour cette mise à jour : recharger l’extension dans chrome://extensions ou brave://extensions suffit.

### Appairage et oubli d’une Apple TV (extension 0.7.1 / compagnon 0.4.2)

Paramètres affiche « Appairée » ou « Non appairée » pour la destination Apple TV enregistrée, même avant une découverte réseau, en consultant les identifiants stockés localement. « Appairée » signifie qu’une association existe sur le Mac ; la TV peut néanmoins refuser des identifiants devenus invalides.

Le bouton « Oublier cette Apple TV » demande une confirmation dans l’extension, ferme une éventuelle diffusion et efface les identifiants et mots de passe locaux des protocoles de ce seul appareil, y compris les configurations en mémoire. La destination par défaut et les associations aux autres appareils sont conservées. Cela ne révoque pas à distance les associations sur la TV. Le bouton « Appairer cette Apple TV » permet ensuite de recommencer avec le code affiché à l’écran.

### Icône et détection (extension 0.8.0)

L’extension dispose d’une icône AirPlay en PNG, fournie en plusieurs résolutions pour la barre d’outils et la page des extensions. L’icône est grise sans source détectée et dorée avec une coche verte quand un flux vidéo HLS/MP4/MKV/WebM est repéré dans l’onglet. Les sous-titres, les pistes audio identifiées et le DASH non pris en charge sont exclus. Chaque onglet possède son propre état. Une navigation réinitialise l’indicateur et une requête de média échouée retire la source correspondante. L’état est également actualisé à l’inspection du lecteur depuis le popup.

Recharge l’extension, puis la page vidéo pour relancer la détection réseau sur une page déjà ouverte. Épingle Airplay Flixer dans la barre d’outils pour voir l’indicateur. Le compagnon ne nécessite pas de redémarrage. Les icônes sont régénérables avec scripts/render_icons.swift.

### Icône vectorielle 0.8.1

Les fichiers extension/icons/{brand,idle,ready}.svg sont les sources vectorielles des icônes, sur un viewBox de 24 × 24. Le pictogramme utilise toute la largeur disponible, avec fond transparent. scripts/render_icons.swift exporte ces SVG en PNG anticrénelés pour les tailles exigées par Chrome. Pour régénérer : swiftc -module-cache-path build/swift-cache scripts/render_icons.swift -o build/render-icons -framework AppKit, puis build/render-icons extension/icons.

### Signal de disponibilité 0.8.2

L’état détecté utilise désormais un écran entièrement vert avec un grand symbole de lecture, pour rester lisible à 16 pixels. L’état sans flux conserve son pictogramme gris. Le SVG ready.svg reste la source des exports PNG.

### Plusieurs sous-titres d’une langue sur Apple TV (extension 0.9.0 / compagnon 0.5.0)

Le choix d’une langue transmet toutes les pistes de ce groupe au compagnon. « Piste au démarrage » choisit la seule piste par défaut ; les autres deviennent des renditions HLS distinctes, à sélectionner depuis le menu Sous-titres de l’Apple TV sans relancer la vidéo. En mode automatique, le groupe de la piste active ou française est utilisé. Les pistes sans indication de langue restent regroupées comme dans l’interface.

La piste initiale est validée avant lancement, puis les alternatives sont téléchargées à la demande et mises en cache (trois téléchargements concurrents au maximum). Une piste alternative indisponible répond 404 sans marquer le flux vidéo comme défaillant. Les pistes intégrées au manifeste sont conservées dans le menu. « Désactivés » garde son comportement explicite. Cette fonction ne change pas les limites DLNA. Le transfert natif refuse explicitement une liste dépassant sa taille autorisée au lieu de supprimer silencieusement des pistes.

La synchronisation externe sonde une seule playlist vidéo, en lisant le start_time du flux vidéo ou les PTS de ses paquets. Cela évite de dépendre uniquement de format.start_time sur un master contenant aussi de l’audio et du WebVTT. Aucun horodatage nul n’est inventé si le flux ne peut pas être sondé. La préparation reste incompatible avec les discontinuités temporelles comme auparavant.

### Recaler une piste externe (extension 0.10.0 / compagnon 0.6.0)

Lecture → Sous-titres → Recaler les sous-titres permet de choisir une piste externe chargée, de régler son décalage entre −600 et +600 secondes (pas rapides de 0,5 seconde, saisie au millième acceptée) et de remettre à zéro. Une valeur négative affiche le texte plus tôt, une valeur positive plus tard. Les boutons ne commandent pas le téléviseur : seul « Appliquer à la lecture » recharge la session à la position actuelle, avec une courte coupure, en activant la piste choisie. Une lecture précédemment en pause est remise en pause après le démarrage du lecteur. Les autres pistes conservent leur propre décalage. Avant une diffusion, le décalage est appliqué à la piste initiale au lancement.

Le texte WebVTT brut est conservé en cache ; les timestamps des cues sont corrigés au moment de servir la piste, sans modifier X-TIMESTAMP-MAP ni cumuler les décalages successifs. Les cues entièrement antérieures à zéro sont supprimées et celles qui croisent zéro sont tronquées. Des URLs révisées empêchent la réutilisation des anciennes pistes en cache après l’application. Les pistes intégrées au flux et DLNA ne disposent pas de ce réglage. Une piste indisponible est détectée avant de toucher à la lecture en cours.

### Synchronisation automatique locale (extension 0.11.0 / compagnon 0.7.0)

Pendant une diffusion Apple TV avec des sous-titres externes, sélectionne une « Piste à recaler », puis « Synchroniser automatiquement ». Le compagnon extrait jusqu’à quatre minutes d’audio autour de la position actuelle, en privilégiant le groupe audio du master HLS quand il existe. Le détecteur WebRTC et l’alignement FFT de ffsubsync 0.5.1 comparent les voix aux intervalles des cues. L’analyse recherche un décalage constant jusqu’à 60 secondes ; elle ne corrige pas une dérive de vitesse ni les différences de montage.

Une proposition n’est affichée que si l’activité vocale et la correspondance sont suffisantes et si les deux parties de l’extrait donnent un décalage cohérent. Les cas incertains sont signalés sans correction. « Utiliser ce décalage » renseigne le champ existant ; seul « Appliquer à la lecture » recharge la vidéo et applique le réglage. Le décalage proposé remplace le décalage manuel, il ne s’y ajoute pas.

L’analyse tourne dans un processus temporaire pour garder le compagnon réactif. Elle peut être annulée et n’est pas interrompue par la fermeture du popup. Arrêter ou changer de diffusion annule le travail et nettoie les fichiers et processus temporaires. L’audio, les identifiants du navigateur et les sous-titres ne sont envoyés à aucun service de reconnaissance externe ; seul le flux d’origine est lu via le relais existant. Les propositions sont liées à une session et à une piste précises. Les pistes intégrées seules et DLNA ne sont pas pris en charge par cette fonction.

### Validation du recalage (extension 0.11.1 / compagnon 0.7.1)

Le contrôle de cohérence vérifie maintenant le même décalage sur les deux moitiés de l’extrait complet, au lieu de recalculer indépendamment un alignement large sur chaque moitié tronquée. Le détecteur vocal utilise le mode WebRTC 3 pour limiter les faux positifs dus au fond sonore. Un passage peu concluant déclenche automatiquement un deuxième extrait distinct si la durée du film le permet. L’interface indique la durée audio réellement analysée ; une validation insuffisante n’est plus présentée comme une preuve d’un montage différent.

Le journal consigne le résultat, les scores et les durées numériques pour diagnostiquer les refus, sans URL, texte des sous-titres ni audio. La correction reste une proposition à appliquer explicitement.

### Interface simplifiée (extension 0.12.0)

L’extension se répartit en trois onglets : Lecture, Sous-titres et Paramètres. Lecture réunit la destination, le mini-contrôleur, les statistiques du flux et le choix de source toujours visible. Le bouton de destination ouvre directement les paramètres. Une nouvelle source reste à lancer explicitement ; la sélection seule ne remplace pas la diffusion.

Sous-titres regroupe les langues et les pistes du prochain lancement. Pendant une diffusion, les outils de synchronisation passent en premier : auto-sync, proposition à sélectionner, puis application explicite. Le réglage manuel reste accessible dans « Ajuster manuellement ». Les paramètres conservent la recherche d’appareils, l’enregistrement du choix par défaut et l’appairage avec possibilité d’oublier l’association. L’interface distingue une destination enregistrée d’une modification non enregistrée.

La mise à jour ne change que l’extension. Recharge-la depuis la page des extensions de Chrome ou Brave ; aucun redémarrage du compagnon n’est requis.

### Protection contre la veille (compagnon 0.7.2)

Pendant une session AirPlay ou DLNA, même en pause, le compagnon demande à macOS d’empêcher la veille automatique du système (PreventUserIdleSystemSleep). L’écran peut toujours s’éteindre. La protection est libérée à la fin, à l’arrêt, au remplacement de la session ou en cas d’erreur ; macOS la supprime aussi à la fermeture du processus. Les réglages d’énergie ne sont pas modifiés. La fermeture du capot et une mise en veille manuelle restent prioritaires.

### Titre de lecture AirPlay (extension 0.12.1 / compagnon 0.7.3)

La détection privilégie le titre Media Session ou Open Graph du lecteur, puis celui de la page principale avant celui d’une iframe. Le compagnon ajoute ce titre au master HLS via EXT-X-SESSION-DATA / com.apple.hls.title et tente une transmission DMAP sur la session RTSP AirPlay existante après le démarrage. Le recalage des sous-titres conserve ce titre. Un refus ou un délai de métadonnées ne provoque pas l’arrêt de la vidéo. L’affichage final dans la télécommande iPhone dépend du traitement de ces données par le récepteur et reste à vérifier sur l’appareil.

Références : [métadonnées de session HLS](https://shaka-project.github.io/shaka-player/docs/api/tutorial-ui.html), [implémentation RTSP de pyatv](https://github.com/postlund/pyatv/blob/master/pyatv/support/rtsp.py).

### Affiche AirPlay (extension 0.12.2 / compagnon 0.7.4)

L’extension récupère les images déclarées par Media Session, Open Graph, Twitter Card ou l’attribut poster du lecteur, en privilégiant la page principale. Au lancement d’une diffusion AirPlay, elle essaie jusqu’à trois candidates, sans cookies ni en-têtes d’authentification. Le téléchargement est limité à 2 Mo par image et la conversion locale produit un JPEG de 180 Ko maximum, avec un côté de 1024 pixels maximum. L’affiche est facultative et retirée du message si elle fait dépasser la limite du transport natif.

Le compagnon garde le JPEG en mémoire pour la session et le sert depuis une URL du relais local. Le master HLS reçoit com.apple.hls.poster ; le canal AirPlay reçoit également une tentative SET_PARAMETER image/jpeg. Un refus de l’image ne coupe pas la lecture. Le recalage des sous-titres conserve la même affiche. L’affichage final dans la télécommande iPhone dépend de tvOS et doit être vérifié sur le récepteur.

### Recherche OpenSubtitles (extension 0.13.0 / compagnon 0.8.0)

Dans Paramètres → OpenSubtitles, renseigne une clé créée sur https://www.opensubtitles.com/consumers. La connexion à un compte OpenSubtitles.com est facultative : elle permet d’utiliser le quota de ce compte. La clé et le jeton de connexion sont enregistrés uniquement dans le dossier privé du compagnon (opensubtitles.json, permissions 0600). Le mot de passe n’est pas enregistré ; la clé et le jeton ne sont jamais renvoyés par le statut. « Retirer la clé et la connexion » supprime ces identifiants locaux.

Sous-titres → Trouver une autre piste propose une recherche par titre ou identifiant IMDb (tt…), langue, film ou saison/épisode. Un marqueur S02E01 ou 2x01 dans le titre préremplit les champs. La requête n’est envoyée qu’au clic sur Rechercher. Les résultats affichent titre, version, FPS et indicateurs disponibles (malentendants, traduction automatique, contributeur vérifié). Les pages suivantes sont chargées explicitement. Aucun résultat n’est présenté comme une correspondance exacte : les flux HLS ne fournissent généralement pas l’empreinte du fichier d’origine.

« Télécharger cette piste » consomme le quota annoncé par OpenSubtitles et sélectionne le fichier pour le prochain lancement. Les pistes sont conservées dans la session de l’extension pour cet onglet et retirées lors d’une navigation ou d’un changement de titre. Le compagnon garde au plus cinq téléchargements en mémoire et réutilise un fichier déjà téléchargé afin d’éviter une nouvelle consommation de quota. Les téléchargements passent uniquement par les domaines HTTPS OpenSubtitles, sans transmettre de clé API ni de jeton à la route de fichier. Les contenus sont bornés à 500 Ko et doivent contenir des horodatages SRT/WebVTT.

Pour une diffusion AirPlay HLS en cours, « Ajouter à la lecture · courte coupure » prépare les horodatages avant de recharger à la position courante, en conservant l’intention de pause. La piste devient disponible pour l’auto-sync et la télécommande. Une source MP4 directe nécessite un nouveau lancement afin de préparer les sous-titres. Les échecs du fournisseur restent distincts des erreurs de lecture. Les quotas, identifiants refusés et limites de débit produisent un message explicite.

Documentation de référence : https://opensubtitles.tawk.help/article/getting-started et https://blog.opensubtitles.com/opensubtitles/how-to-build-a-movie-subtitle-finder-cli-with-python-and-the-opensubtitles-api. Sans clé utilisateur, le parcours est vérifié par des réponses simulées ; une recherche et un téléchargement réels restent à confirmer après configuration.

### Catalogue SubDL (extension 0.14.0 / compagnon 0.9.0)

Sous-titres → Trouver une autre piste → Catalogue permet de choisir OpenSubtitles ou SubDL. Paramètres → SubDL reçoit une clé API créée sur https://subdl.com/panel/api. Elle est conservée dans subdl.json avec permissions 0600 et n’est jamais renvoyée dans le statut. Les deux catalogues ont leurs configurations et caches distincts. L’interface désactive la configuration SubDL si le compagnon actif ne connaît pas encore ce fournisseur.

La recherche SubDL V1 utilise titre/IMDb, langue et saison/épisode, demande les fichiers détaillés avec unpack=1 et affiche jusqu’à 100 pistes SRT/WebVTT. Cette API documente une limite de résultats mais pas de pagination : la page suivante est masquée pour SubDL ; affiner le titre ou utiliser l’identifiant IMDb permet de préciser la recherche. Les fichiers détaillés sont filtrés selon l’épisode et la langue. Les archives ZIP sont lues uniquement en mémoire, avec limites de taille et de nombre d’entrées ; une archive ambiguë n’est pas choisie automatiquement. Les formats ASS/SUB ne sont pas convertis par cette intégration.

Les téléchargements acceptent uniquement les routes HTTPS dl.subdl.com/subtitle/ et ne transmettent pas la clé de recherche aux fichiers. Le téléchargement est limité à 2 Mo et le sous-titre décompressé à 500 Ko. Une piste choisie suit le même parcours que les fichiers OpenSubtitles : prochain lancement, ou ajout explicite à la session AirPlay HLS courante avec courte coupure, puis auto-sync. La distinction fournisseur + identifiant évite les collisions entre catalogues.

Référence : https://subdl.com/api-doc/. Le parcours API est testé avec des réponses simulées ; une recherche réelle nécessite la clé SubDL de l’utilisateur.

### Télécharger la vidéo (extension 0.15.1 / compagnon 0.10.1)

Dans **Lecture**, sélectionne la source puis clique sur **Télécharger la vidéo**. Le compagnon enregistre le fichier dans **Téléchargements** et continue après la fermeture du popup. Rouvre-le pour suivre le transfert ou l’annuler.

Les fichiers MP4, MOV, MKV et WebM sont copiés directement. FFmpeg assemble les vidéos HLS à la demande en MP4 sans réencodage. Un flux HLS principal utilise sa meilleure variante et la piste audio par défaut lorsqu’elle est disponible. La piste choisie dans **Sous-titres**, y compris une piste importée, est enregistrée en SRT à côté de la vidéo avec le même nom. Le réglage **Désactivés** télécharge uniquement la vidéo. Les flux en direct, DASH et les sources protégées par DRM sont hors du périmètre.
