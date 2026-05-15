# Architecture ERP ScolariPay

Ce document illustre la structure des données relationnelles pour gérer les classes et les tarifs, ainsi que le flux métier pour les relances par email et l'émission de reçus PDF.

## Modèle de Données (MCD)

```mermaid
erDiagram
    ETABLISSEMENT ||--o{ CLASSE : "définit"
    ETABLISSEMENT ||--o{ ELEVE : "scolarise"
    ETABLISSEMENT ||--o{ PAIEMENT : "encaisse"
    
    CLASSE ||--o{ ELEVE : "contient"
    ELEVE ||--o{ PAIEMENT : "règle"
    
    ETABLISSEMENT {
        int id
        string nom
        string type_abonnement
        string statut
    }
    
    CLASSE {
        int id
        string nom
        float montant_inscription
        float montant_scolarite
    }
    
    ELEVE {
        int id
        string nom
        string prenom
        string nom_parent
        string email_parent
        int classe_id FK
    }
    
    PAIEMENT {
        int id
        float montant
        string type_paiement
        datetime date_paiement
        int eleve_id FK
    }
```

## Flux Métier : Relance et Facturation (PDF)

```mermaid
sequenceDiagram
    participant Comptable
    participant Serveur
    participant Email_Parent as Email (Parent)
    participant Fichier_PDF as Fichier PDF
    
    %% Cas d'une relance
    Comptable->>Serveur: Clic "Relancer" sur élève en retard
    Serveur->>Serveur: Calcule Total Dû (Tarifs Classe) - Total Payé
    Serveur->>Email_Parent: Envoi SMTP (Flask-Mail) avec solde restant
    Serveur-->>Comptable: Notification de succès "Email envoyé"
    
    %% Cas d'un reçu PDF
    Comptable->>Serveur: Clic "Télécharger PDF" sur un paiement
    Serveur->>Serveur: Requête DB (Infos Élève, École, Paiement)
    Serveur->>Fichier_PDF: FPDF dessine le layout (Logo, Montant, Solde)
    Fichier_PDF-->>Comptable: Téléchargement direct du reçu (.pdf)
```
