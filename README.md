# Outil de supervision du comptage voyageurs

## Présentation du projet

Ce dépôt contient le développement d'un **outil interne de supervision** du système de comptage voyageurs déployé sur le parc bus et tramways de **TaM (Transports de l'agglomération de Montpellier)**.

L'outil permet aux équipes de maintenance de repérer rapidement les véhicules ou capteurs en anomalie, sans avoir besoin d'un accès direct à la base de données technique, et de retrouver immédiatement la procédure de maintenance associée à l'anomalie détectée.

### Contexte

Depuis la mise en place de la gratuité des transports sur le réseau TaM fin 2023, la billettique ne permet plus d'estimer la fréquentation réelle du réseau. TaM a donc engagé un projet structurant avec le groupement **Webreathe**, qui a équipé l'ensemble du parc d'un système embarqué de comptage automatique des voyageurs, composé de :

- capteurs **EYES** positionnés au-dessus des portes,
- un calculateur central **WEBOX** par véhicule, assurant le traitement local et la remontée des données via 4G vers le serveur Webreathe,
- une interface avec le SAE pour contextualiser chaque événement de comptage (ligne, arrêt, sens, course).

Cette architecture, aussi fiable soit-elle, nécessite un suivi technique régulier : antennes, cartes SIM, câblages, calibrage des capteurs... C'est pour faciliter ce suivi que cet outil de supervision est développé, en complément de l'outil CARE3 fourni par Webreathe.

### Objectifs de l'outil

1. **Vue d'ensemble du parc**
   - Visualiser en un coup d'œil l'état de chaque véhicule (fonctionnel / anomalie)
   - Filtrer et trier par véhicule, par état, par date de dernière remontée

2. **Détection automatique des anomalies**
   - Anomalie véhicule : aucune remontée WEBOX depuis plus de 2 jours
   - Anomalie porte : un capteur EYES silencieux alors que les autres portes du même véhicule remontent des données
   - (à venir) Incohérence de remontée et anomalie de position GPS

3. **Aide à l'intervention terrain**
   - Rappel automatique du cas correspondant dans la procédure de maintenance Webreathe (WEBOX / EYES)
   - Mode de vérification post-intervention : confirmation rapide qu'une porte ou un véhicule remonte de nouveau des données après une réparation

4. **Historique et traçabilité**
   - Consultation de l'historique des remontées sur une période donnée (jusqu'à 1 mois), pour vérifier la continuité des transmissions

### Avancement actuel

- ✅ Connexion à la base de données technique **BDD3** (tables `metrics` et `door_counts`)
- ✅ Vue globale du parc : liste des véhicules, état, dernière remontée
- ✅ Filtres (état, numéro de véhicule) et tri des colonnes (véhicule, dernière remontée)
- ✅ Vue détaillée par véhicule : état de chaque porte
- ✅ Détection d'anomalie véhicule (seuil : 2 jours sans remontée)
- ✅ Détection d'anomalie porte (silence relatif aux autres portes du véhicule)
- ✅ Rappel automatique de la procédure de maintenance selon le type d'anomalie
- ✅ Mode de vérification post-intervention (rafraîchissement ciblé toutes les 30 secondes)
- ✅ Historique des remontées filtrable par période (fenêtre de données limitée à 1 mois)
- ✅ Détection d'anomalie SAE et GPS
- ✅ Distinction entre véhicule réellement en panne et véhicule hors exploitation
- ⏳ Onglet statistiques pour les mainteneurs (état du parc, répartition par type, nouvelles anomalies, anomalies qui traînent, durée des anomalies)
- ⏳ Authentification
- ⏳ Déploiement sur serveur
- ⏳ Mise en forme visuelle

### Auteur

- **[Johan COUSIN](https://github.com/Predators972)** — Encadrant Technique - Service Installations Fixes, TaM

### Encadrement

- Emmanuel AHIVI — Responsable Unité Système - Service Installations Fixes, TaM

---

## Structure du projet

```text
passenger-counting-supervision/
│
├── backend/                         # API FastAPI (Python)
│   ├── app/
│   │   ├── routes/
│   │   │   ├── __init__.py
│   │   │   ├── stats.py             # Endpoint statistiques (/api/stats/lingering)
│   │   │   └── vehicles.py          # Endpoints API (/api/vehicles, /api/history, ...)
│   │   ├── __init__.py
│   │   ├── anomaly.py               # Logique de détection d'anomalie
│   │   ├── config.py                # Chargement des identifiants + seuils d'anomalie
│   │   ├── database.py              # Connexion BDD3 + repli sur données d'exemple
│   │   ├── fleet_reference.py       # Référence matériel roulant (type, portes, numérotation)
│   │   └── main.py                  # Point d'entrée, sert aussi le frontend
│   ├── .env.example                 # Modèle pour les identifiants (à copier en .env)
│   └── requirements.txt
│
├── data/                            # Données d'exemple pour développer sans accès BDD3
│   ├── rolling_stock_ranges.json    # Plages de numéros -> type de matériel roulant et portes
│   ├── sample_door_counts.csv
│   └── sample_metrics.csv
│
├── frontend/                        # Interface web (HTML / CSS / JS, sans framework)
│   ├── app.js
│   ├── index.html
│   └── style.css
│
├── .gitignore
├── Doxyfile                         # Configuration Doxygen pour générer la doc du code
└── README.md
```

---

## Démarrage rapide

### Prérequis

- Python 3.14 (ou version ultérieure)
- Accès à la base de données **BDD3** (identifiants à demander à la DSI / visibles dans DBeaver)

### Installation

Depuis le dossier `backend/` :

```bash
py -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Éditer ensuite le fichier `.env` :
- pour développer sans BDD3 (données d'exemple) : laisser `USE_SAMPLE_DATA=true`
- pour se connecter à la vraie base : renseigner `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, puis passer `USE_SAMPLE_DATA=false`

### Lancer l'outil

Depuis le dossier `backend/` (environnement virtuel activé) :

```bash
uvicorn app.main:app --reload
```

Puis ouvrir : **http://127.0.0.1:8000**

La documentation interactive de l'API est disponible sur **http://127.0.0.1:8000/docs**.

---

## Pile technique

### Backend
- **Python 3.14** + **FastAPI** — API REST
- **psycopg2** — connexion PostgreSQL à BDD3
- **pandas** — calcul des indicateurs et détection d'anomalie

### Frontend
- **HTML / CSS / JavaScript** natifs, sans framework ni étape de build

### Base de données
- **PostgreSQL** — base **BDD3**, tables `metrics` (remontées Webreathe/SAE) et `door_counts` (comptages bruts par porte)

---

## Documents de référence

Ce projet s'appuie sur les documents internes suivants :

- **Cahier des charges de l'outil de supervision** — exigences fonctionnelles, structure des données, règles d'anomalie
- **Procédure de maintenance Webreathe (WEBOX / EYES)** — diagnostic et actions correctives sur le terrain

## Sécurité

Le fichier `.env` contient des identifiants de connexion réels à BDD3. Il est exclu du suivi Git via `.gitignore` et ne doit **jamais** être partagé, ni ajouté manuellement à un commit.
