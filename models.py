from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class Etablissement(db.Model):
    __tablename__ = 'etablissements'
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    type_abonnement = db.Column(db.String(20), nullable=False) # 'Mensuel' ou 'Annuel'
    date_expiration = db.Column(db.DateTime, nullable=False)
    statut = db.Column(db.String(20), default='Actif') # 'Actif' ou 'Suspendu'
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)
    est_demo = db.Column(db.Boolean, default=False)

    # Relations (One-to-Many)
    utilisateurs = db.relationship('Utilisateur', backref='etablissement', lazy=True)
    eleves = db.relationship('Eleve', backref='etablissement', lazy=True)
    paiements = db.relationship('Paiement', backref='etablissement', lazy=True)

class Utilisateur(db.Model):
    __tablename__ = 'utilisateurs'
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    mot_de_passe = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False) # 'SuperAdmin', 'Admin', 'Comptable'
    
    # etablissement_id est NULL pour le SuperAdmin (qui gère l'app)
    etablissement_id = db.Column(db.Integer, db.ForeignKey('etablissements.id'), nullable=True)

class Eleve(db.Model):
    __tablename__ = 'eleves'
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(100), nullable=False)
    prenom = db.Column(db.String(100), nullable=False)
    classe = db.Column(db.String(50), nullable=False)
    
    # Multi-tenancy: Chaque élève appartient à une école spécifique
    etablissement_id = db.Column(db.Integer, db.ForeignKey('etablissements.id'), nullable=False)

class Paiement(db.Model):
    __tablename__ = 'paiements'
    id = db.Column(db.Integer, primary_key=True)
    montant = db.Column(db.Float, nullable=False)
    date_paiement = db.Column(db.DateTime, default=datetime.utcnow)
    motif = db.Column(db.String(200), nullable=False)
    
    eleve_id = db.Column(db.Integer, db.ForeignKey('eleves.id'), nullable=False)
    eleve = db.relationship('Eleve', backref=db.backref('paiements', lazy=True))
    
    # Multi-tenancy: Chaque paiement est aussi associé à l'établissement (sécurité supplémentaire)
    etablissement_id = db.Column(db.Integer, db.ForeignKey('etablissements.id'), nullable=False)
