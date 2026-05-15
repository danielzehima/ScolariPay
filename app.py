from flask import Flask, request, redirect, url_for, session, flash, abort, render_template
from models import db, Etablissement, Utilisateur, Eleve, Paiement
from datetime import datetime, timedelta
import os
import random
import string

app = Flask(__name__)
# Clé secrète pour les sessions
app.config['SECRET_KEY'] = 'ma_cle_secrete_dev' 
# Base de données SQLite pour le développement
basedir = os.path.abspath(os.path.dirname(__name__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'saas_dev.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

# ==============================================================================
# MIDDLEWARE : Vérification de l'Abonnement et du Multi-Tenancy
# ==============================================================================
@app.before_request
def check_abonnement_et_tenant():
    """
    Exécuté avant chaque requête.
    Vérifie l'expiration de l'abonnement et bloque l'accès aux fonctions sensibles (ex: paiements).
    """
    if 'user_id' in session:
        user_id = session['user_id']
        etablissement_id = session.get('etablissement_id')
        
        # Ignorer le SuperAdmin pour ces vérifications
        if session.get('user_role') == 'SuperAdmin':
            return
            
        if etablissement_id:
            ecole = Etablissement.query.get(etablissement_id)
            if ecole:
                # 1. Vérification Démo 24h
                if ecole.est_demo:
                    heures_ecoulees = (datetime.utcnow() - ecole.date_creation).total_seconds() / 3600
                    if heures_ecoulees >= 24 and ecole.statut != 'Suspendu':
                        ecole.statut = 'Suspendu'
                        db.session.commit()
                        
                    if ecole.statut == 'Suspendu':
                        allowed_endpoints = ['fin_demo', 'logout', 'static']
                        if request.endpoint not in allowed_endpoints:
                            return redirect(url_for('fin_demo'))

                # 2. Vérification abonnement classique
                is_expired = ecole.date_expiration < datetime.utcnow()
                is_suspended = ecole.statut == 'Suspendu'
                
                if (is_expired or is_suspended) and not ecole.est_demo:
                    allowed_endpoints = ['ecole_dashboard', 'logout', 'static']
                    if request.endpoint not in allowed_endpoints:
                        flash("Action bloquée : Votre abonnement est expiré ou suspendu. Veuillez renouveler.", "danger")
                        return redirect(url_for('ecole_dashboard'))

# ==============================================================================
# ROUTES
# ==============================================================================
@app.route('/')
def index():
    if 'user_id' in session:
        if session.get('user_role') == 'SuperAdmin':
            return redirect(url_for('superadmin_dashboard'))
        else:
            return redirect(url_for('ecole_dashboard'))
    return render_template('landing.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password') # À sécuriser avec hachage (ex: werkzeug.security) en prod
        
        user = Utilisateur.query.filter_by(email=email).first()
        if user and user.mot_de_passe == password:
            # Initialisation de la session
            session['user_id'] = user.id
            session['user_role'] = user.role
            session['etablissement_id'] = user.etablissement_id
            
            if user.role == 'SuperAdmin':
                return redirect(url_for('superadmin_dashboard'))
            else:
                return redirect(url_for('ecole_dashboard'))
                
        flash("Identifiants incorrects.", "danger")
        
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/superadmin')
def superadmin_dashboard():
    # Protection de route
    if session.get('user_role') != 'SuperAdmin':
        abort(403) # Interdit
        
    etablissements = Etablissement.query.all()
    return render_template('superadmin_dashboard.html', etablissements=etablissements)

@app.route('/superadmin/ajouter_ecole', methods=['POST'])
def ajouter_ecole():
    if session.get('user_role') != 'SuperAdmin':
        abort(403)
        
    nom = request.form.get('nom')
    email = request.form.get('email')
    type_abonnement = request.form.get('type_abonnement')
    password = request.form.get('password')
    
    # Calcul de la date d'expiration
    if type_abonnement == 'Annuel':
        expiration = datetime.utcnow() + timedelta(days=365)
    else:
        expiration = datetime.utcnow() + timedelta(days=30)
        
    # Création de l'établissement
    nouvelle_ecole = Etablissement(
        nom=nom,
        email=email,
        type_abonnement=type_abonnement,
        date_expiration=expiration,
        statut='Actif'
    )
    db.session.add(nouvelle_ecole)
    db.session.flush() # Pour récupérer l'ID
    
    # Création du compte Admin de l'école
    nouvel_admin = Utilisateur(
        nom=f"Admin {nom}",
        email=email, # On réutilise l'email de l'école pour le login admin
        mot_de_passe=password,
        role='Admin',
        etablissement_id=nouvelle_ecole.id
    )
    db.session.add(nouvel_admin)
    db.session.commit()
    
    flash(f"L'école {nom} a été créée avec succès !", "success")
    return redirect(url_for('superadmin_dashboard'))

@app.route('/superadmin/editer_ecole/<int:id>', methods=['POST'])
def editer_ecole(id):
    if session.get('user_role') != 'SuperAdmin':
        abort(403)
        
    ecole = Etablissement.query.get_or_404(id)
    ecole.nom = request.form.get('nom')
    
    nouveau_type = request.form.get('type_abonnement')
    if nouveau_type != ecole.type_abonnement:
        ecole.type_abonnement = nouveau_type
        # Prolongation automatique selon le nouveau type
        if nouveau_type == 'Annuel':
            ecole.date_expiration = datetime.utcnow() + timedelta(days=365)
        else:
            ecole.date_expiration = datetime.utcnow() + timedelta(days=30)
            
    ecole.statut = request.form.get('statut')
    db.session.commit()
    
    flash(f"L'école {ecole.nom} a été mise à jour.", "success")
    return redirect(url_for('superadmin_dashboard'))

@app.route('/ecole')
def ecole_dashboard():
    # Protection de route et Multi-tenancy
    if not session.get('etablissement_id'):
        abort(403)
        
    ecole = Etablissement.query.get(session['etablissement_id'])
    est_expire = ecole.date_expiration < datetime.utcnow()
    total_paiements = sum(p.montant for p in ecole.paiements)
    
    temps_restant = None
    if ecole.est_demo and ecole.statut != 'Suspendu':
        heures_ecoulees = (datetime.utcnow() - ecole.date_creation).total_seconds() / 3600
        temps_restant = max(0, int(24 - heures_ecoulees))
    
    return render_template('ecole_dashboard.html', ecole=ecole, est_expire=est_expire, total_paiements=total_paiements, temps_restant=temps_restant)

@app.route('/demarrer-demo')
def demarrer_demo():
    # Génération d'un nom et email aléatoires pour la démo
    suffix = ''.join(random.choices(string.digits, k=4))
    nom = f"École Démo {suffix}"
    email = f"demo{suffix}@ecole.fr"
    password = "demo"
    
    nouvelle_ecole = Etablissement(
        nom=nom,
        email=email,
        type_abonnement='Mensuel',
        date_expiration=datetime.utcnow() + timedelta(days=30),
        statut='Actif',
        est_demo=True,
        date_creation=datetime.utcnow()
    )
    db.session.add(nouvelle_ecole)
    db.session.flush()
    
    nouvel_admin = Utilisateur(
        nom=f"Directeur {nom}",
        email=email,
        mot_de_passe=password,
        role='Admin',
        etablissement_id=nouvelle_ecole.id
    )
    db.session.add(nouvel_admin)
    db.session.commit()
    
    session['user_id'] = nouvel_admin.id
    session['user_role'] = nouvel_admin.role
    session['etablissement_id'] = nouvelle_ecole.id
    
    flash(f"Bienvenue ! Voici votre compte démo. Il est valide pendant 24 heures.", "success")
    return redirect(url_for('ecole_dashboard'))

@app.route('/fin-demo')
def fin_demo():
    return render_template('fin_demo.html')

@app.route('/demander_demo', methods=['POST'])
def demander_demo():
    nom = request.form.get('nom_ecole')
    email = request.form.get('email_contact')
    # On simule l'envoi d'un mail ou l'enregistrement d'un lead
    flash(f"Merci ! Votre demande pour l'établissement '{nom}' a bien été reçue. Notre équipe vous contactera à {email} rapidement.", "success")
    # Redirection vers la landing page avec ancre
    return redirect(url_for('index') + '#contact')

@app.route('/ecole/ajouter_eleve', methods=['POST'])
def ajouter_eleve():
    etablissement_id = session.get('etablissement_id')
    if not etablissement_id:
        abort(403)
        
    ecole = Etablissement.query.get(etablissement_id)
    # Double vérification de la validité de l'abonnement
    if ecole.date_expiration < datetime.utcnow() or ecole.statut == 'Suspendu':
        flash("Action impossible : abonnement inactif.", "danger")
        return redirect(url_for('ecole_dashboard'))
        
    nom = request.form.get('nom')
    prenom = request.form.get('prenom')
    classe = request.form.get('classe')
    
    nouvel_eleve = Eleve(
        nom=nom,
        prenom=prenom,
        classe=classe,
        etablissement_id=etablissement_id
    )
    db.session.add(nouvel_eleve)
    db.session.commit()
    
    flash(f"L'élève {prenom} {nom} a été ajouté avec succès !", "success")
    return redirect(url_for('ecole_dashboard'))

@app.route('/ecole/editer_eleve/<int:id>', methods=['POST'])
def editer_eleve(id):
    etablissement_id = session.get('etablissement_id')
    if not etablissement_id:
        abort(403)
        
    eleve = Eleve.query.get_or_404(id)
    # Vérification stricte Multi-Tenant
    if eleve.etablissement_id != etablissement_id:
        abort(403)
        
    eleve.nom = request.form.get('nom')
    eleve.prenom = request.form.get('prenom')
    eleve.classe = request.form.get('classe')
    
    db.session.commit()
    flash(f"L'élève {eleve.prenom} {eleve.nom} a été mis à jour.", "success")
    return redirect(url_for('ecole_dashboard'))

@app.route('/ecole/ajouter_paiement', methods=['POST'])
def ajouter_paiement():
    etablissement_id = session.get('etablissement_id')
    if not etablissement_id:
        abort(403)
        
    ecole = Etablissement.query.get(etablissement_id)
    # Vérification stricte : bloquer le paiement si l'abonnement est inactif
    if ecole.date_expiration < datetime.utcnow() or ecole.statut == 'Suspendu':
        flash("Action bloquée : abonnement inactif. Vous ne pouvez pas encaisser de paiements.", "danger")
        return redirect(url_for('ecole_dashboard'))
        
    eleve_id = request.form.get('eleve_id')
    montant = request.form.get('montant')
    motif = request.form.get('motif')
    
    nouveau_paiement = Paiement(
        montant=float(montant),
        motif=motif,
        eleve_id=eleve_id,
        etablissement_id=etablissement_id
    )
    db.session.add(nouveau_paiement)
    db.session.commit()
    
    flash("Le paiement a été encaissé avec succès.", "success")
    return redirect(url_for('ecole_dashboard'))

# Exemple d'une route bloquée si abonnement expiré
@app.route('/ecole/paiements', methods=['GET', 'POST'])
def gerer_paiements():
    return "Page de gestion des paiements. (Cette page n'est accessible que si l'abonnement est actif)."

# ==============================================================================
# INITIALISATION
# ==============================================================================
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        # Création de l'utilisateur SuperAdmin par défaut pour commencer à tester
        if not Utilisateur.query.filter_by(role='SuperAdmin').first():
            superadmin = Utilisateur(
                nom='Super Admin', 
                email='admin@saas.com', 
                mot_de_passe='admin', # À changer
                role='SuperAdmin'
            )
            db.session.add(superadmin)
            db.session.commit()
            print("Compte SuperAdmin créé : admin@saas.com / admin")
            
    app.run(debug=True, port=5000)
