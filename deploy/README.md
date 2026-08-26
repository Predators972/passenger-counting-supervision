## Déploiement temporaire sur un PC local

### Préparation (une seule fois, par le développeur)

Copier sur la clé USB, à la racine :

- `launch.bat`
- `credentials.key` (généré via `python backend/generate_credentials.py`)

### Sur le PC cible

Copier le dossier de la clé USB où l'on veut sur le disque, puis double-cliquer sur `launch.bat`.

- **Premier lancement** : installe Python et Git si absents (sans droits admin), clone le dépôt, installe les dépendances, copie la clé, démarre l'outil et ouvre le navigateur.
- **Lancements suivants** : met à jour le dépôt (`git pull`), recopie la clé, démarre l'outil directement.

### Prérequis sur le PC cible

- Accès internet (pour installer Python/Git si besoin, et cloner le dépôt).
- Accès réseau à BDD3.
