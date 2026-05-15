---
trigger: always_on
---

# Règles du Projet : SaaS B2B Scolarité

## Stack Technique Obligatoire
- **Backend :** Python 3.11 / **Flask**.
- **Frontend :** HTML5 / **Bootstrap 5**.
- **Base de données :** SQLite (dev) / PostgreSQL (prod) avec **Flask-SQLAlchemy**.
- **Multi-Tenancy :** Chaque table (Élèves, Paiements, etc.) doit avoir une clé étrangère `ecole_id`.

## Logique Business (SaaS)
- **Modèle Client :** Un client est un "Établissement" (École).
- **Abonnements :** Gérer les plans (Mensuel / Annuel) et la date d'expiration de l'accès pour chaque établissement.
- **Rôles :** SuperAdmin (Moi), Admin École, Comptable École.

## Interface & Langue
- Dashboard distinct pour le SuperAdmin (gestion des écoles abonnées).
- Dashboard pour l'école (gestion des élèves et paiements).
- Langue : **Français**.