from flask import Flask, request, redirect, url_for, session, flash, abort, render_template, send_file
from models import db, Etablissement, Utilisateur, Eleve, Paiement, Classe
from datetime import datetime, timedelta
import os
import random
import string
import csv
import io
from fpdf import FPDF
from werkzeug.security import generate_password_hash, check_password_hash
from flask_migrate import Migrate

app = Flask(__name__)
# Clé secrète pour les sessions
app.config['SECRET_KEY'] = 'ma_cle_secrete_dev' 
# Base de données SQLite pour le développement
basedir = os.path.abspath(os.path.dirname(__name__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'saas_dev_v2.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Configuration Email (Pour le système de relance)
app.config['MAIL_SERVER'] = 'smtp.gmail.com' # À configurer avec un vrai SMTP en prod
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'contact@scolaripay.com'
app.config['MAIL_PASSWORD'] = 'motdepasse_smtp'
app.config['MAIL_DEFAULT_SENDER'] = 'contact@scolaripay.com'

db.init_app(app)
migrate = Migrate(app, db)

# Ne pas oublier d'importer Mail plus haut si possible, sinon on l'importe ici :
from flask_mail import Mail, Message
mail = Mail(app)

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
        password = request.form.get('password')
        
        user = Utilisateur.query.filter_by(email=email).first()
        # Vérification sécurisée du mot de passe
        if user and check_password_hash(user.mot_de_passe, password):
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

@app.route('/inscription', methods=['GET', 'POST'])
def inscription():
    if request.method == 'POST':
        nom = request.form.get('nom')
        email = request.form.get('email')
        password = request.form.get('password')
        devise = request.form.get('devise', 'FCFA')
        
        # Vérifier si l'email existe déjà
        if Utilisateur.query.filter_by(email=email).first():
            flash("Cet email est déjà utilisé. Veuillez vous connecter.", "danger")
            return redirect(url_for('inscription'))
            
        nouvelle_ecole = Etablissement(
            nom=nom,
            email=email,
            type_abonnement='Mensuel',
            date_expiration=datetime.utcnow() + timedelta(days=30),
            statut='Actif',
            est_demo=False,
            devise=devise
        )
        db.session.add(nouvelle_ecole)
        db.session.flush()
        
        nouvel_admin = Utilisateur(
            nom=f"Directeur {nom}",
            email=email,
            mot_de_passe=generate_password_hash(password),
            role='Admin',
            etablissement_id=nouvelle_ecole.id
        )
        db.session.add(nouvel_admin)
        db.session.commit()
        
        # Connexion automatique
        session['user_id'] = nouvel_admin.id
        session['user_role'] = nouvel_admin.role
        session['etablissement_id'] = nouvelle_ecole.id
        
        flash("Inscription réussie ! Bienvenue sur votre nouvel espace de gestion.", "success")
        return redirect(url_for('ecole_dashboard'))
        
    return render_template('inscription.html')

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
    
    devise = request.form.get('devise', 'FCFA')
    
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
        statut='Actif',
        devise=devise
    )
    db.session.add(nouvelle_ecole)
    db.session.flush() # Pour récupérer l'ID
    
    # Création du compte Admin de l'école avec hachage
    nouvel_admin = Utilisateur(
        nom=f"Admin {nom}",
        email=email,
        mot_de_passe=generate_password_hash(password),
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
    ecole.devise = request.form.get('devise', ecole.devise)
    
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
    etablissement_id = session.get('etablissement_id')
    if not etablissement_id:
        abort(403)
        
    ecole = Etablissement.query.get(etablissement_id)
    if not ecole:
        session.clear()
        return redirect(url_for('login'))
        
    est_expire = ecole.date_expiration < datetime.utcnow()
    total_paiements = sum(p.montant for p in ecole.paiements)
    
    # S'assurer que tous les élèves ont un code parent (pour la rétrocompatibilité)
    eleves_modifies = False
    for eleve in ecole.eleves:
        if not eleve.code_parent:
            eleve.code_parent = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
            eleves_modifies = True
    if eleves_modifies:
        db.session.commit()
    
    # Récupérer les classes pour le select
    classes = Classe.query.filter_by(etablissement_id=ecole.id).all()
    
    # Logique pour les impayés et le Taux de Recouvrement
    eleves_en_retard = []
    total_attendu = 0
    for eleve in ecole.eleves:
        total_du = eleve.classe_associee.montant_inscription + eleve.classe_associee.montant_scolarite
        total_attendu += total_du
        
        total_paye = sum(p.montant for p in eleve.paiements)
        reste_a_payer = total_du - total_paye
        if reste_a_payer > 0:
            eleve.reste_a_payer = reste_a_payer
            eleves_en_retard.append(eleve)
            
    taux_recouvrement = 0
    if total_attendu > 0:
        taux_recouvrement = round((total_paiements / total_attendu) * 100)
        
    # Graphique des 7 derniers jours
    import json
    chart_labels = []
    chart_data = []
    aujourdhui = datetime.utcnow().date()
    # On prépare les 7 derniers jours (du plus ancien au plus récent)
    dict_paiements = {aujourdhui - timedelta(days=i): 0 for i in range(6, -1, -1)}
    
    for paiement in ecole.paiements:
        date_p = paiement.date_paiement.date()
        if date_p in dict_paiements:
            dict_paiements[date_p] += paiement.montant
            
    for date_jour, montant in dict_paiements.items():
        chart_labels.append(date_jour.strftime('%d/%m'))
        chart_data.append(montant)
    
    temps_restant = None
    if ecole.est_demo and ecole.statut != 'Suspendu':
        heures_ecoulees = (datetime.utcnow() - ecole.date_creation).total_seconds() / 3600
        temps_restant = max(0, int(24 - heures_ecoulees))
    
    return render_template('ecole_dashboard.html', 
                           ecole=ecole, 
                           est_expire=est_expire, 
                           total_paiements=total_paiements, 
                           temps_restant=temps_restant, 
                           classes=classes, 
                           eleves_en_retard=eleves_en_retard,
                           taux_recouvrement=taux_recouvrement,
                           total_attendu=total_attendu,
                           chart_labels=json.dumps(chart_labels),
                           chart_data=json.dumps(chart_data))

@app.route('/ecole/export_rapport')
def export_rapport():
    if not session.get('etablissement_id'):
        abort(403)
        
    ecole = Etablissement.query.get(session['etablissement_id'])
    est_expire = ecole.date_expiration < datetime.utcnow()
    if est_expire or ecole.statut == 'Suspendu':
        flash("Action bloquée : Abonnement inactif.", "danger")
        return redirect(url_for('ecole_dashboard'))
        
    si = io.StringIO()
    # On utilise le point-virgule comme séparateur, plus pratique pour Excel en français
    writer = csv.writer(si, delimiter=';')
    
    # En-têtes
    writer.writerow(['Nom', 'Prénom', 'Classe', 'Parent', 'Email Parent', 'Total Dû', 'Total Payé', 'Reste à Payer', 'Devise'])
    
    # Données
    for eleve in ecole.eleves:
        total_du = eleve.classe_associee.montant_inscription + eleve.classe_associee.montant_scolarite
        total_paye = sum(p.montant for p in eleve.paiements)
        reste_a_payer = total_du - total_paye
        
        writer.writerow([
            eleve.nom,
            eleve.prenom,
            eleve.classe_associee.nom,
            eleve.nom_parent or '',
            eleve.email_parent or '',
            total_du,
            total_paye,
            reste_a_payer,
            ecole.devise
        ])
        
    # Conversion en BytesIO avec encodage utf-8-sig (BOM) pour qu'Excel reconnaisse bien les accents
    output = io.BytesIO()
    output.write(si.getvalue().encode('utf-8-sig'))
    output.seek(0)
    
    date_str = datetime.utcnow().strftime('%Y-%m-%d')
    return send_file(
        output,
        mimetype='text/csv',
        as_attachment=True,
        download_name=f"rapport_ecole_{date_str}.csv"
    )

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
        date_creation=datetime.utcnow(),
        devise='FCFA'
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

@app.route('/ecole/ajouter_classe', methods=['POST'])
def ajouter_classe():
    etablissement_id = session.get('etablissement_id')
    if not etablissement_id:
        abort(403)
    if session.get('user_role') != 'Admin':
        flash("Accès refusé : Seul l'administrateur peut configurer les classes.", "danger")
        return redirect(url_for('ecole_dashboard'))
        
    nom = request.form.get('nom')
    montant_inscription = float(request.form.get('montant_inscription', 0))
    montant_scolarite = float(request.form.get('montant_scolarite', 0))
    
    nouvelle_classe = Classe(
        nom=nom,
        montant_inscription=montant_inscription,
        montant_scolarite=montant_scolarite,
        etablissement_id=etablissement_id
    )
    db.session.add(nouvelle_classe)
    db.session.commit()
    flash(f"La classe {nom} a été créée avec succès.", "success")
    return redirect(url_for('ecole_dashboard'))

@app.route('/ecole/ajouter_eleve', methods=['POST'])
def ajouter_eleve():
    etablissement_id = session.get('etablissement_id')
    if not etablissement_id:
        abort(403)
        
    ecole = Etablissement.query.get(etablissement_id)
    if ecole.date_expiration < datetime.utcnow() or ecole.statut == 'Suspendu':
        flash("Action impossible : abonnement inactif.", "danger")
        return redirect(url_for('ecole_dashboard'))
        
    nom = request.form.get('nom')
    prenom = request.form.get('prenom')
    nom_parent = request.form.get('nom_parent')
    email_parent = request.form.get('email_parent')
    classe_id = request.form.get('classe_id')
    
    nouvel_eleve = Eleve(
        nom=nom,
        prenom=prenom,
        nom_parent=nom_parent,
        email_parent=email_parent,
        classe_id=classe_id,
        etablissement_id=etablissement_id,
        code_parent=''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
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
    if eleve.etablissement_id != etablissement_id:
        abort(403)
        
    eleve.nom = request.form.get('nom')
    eleve.prenom = request.form.get('prenom')
    eleve.nom_parent = request.form.get('nom_parent')
    eleve.email_parent = request.form.get('email_parent')
    eleve.classe_id = request.form.get('classe_id')
    
    db.session.commit()
    flash(f"L'élève {eleve.prenom} {eleve.nom} a été mis à jour.", "success")
    return redirect(url_for('ecole_dashboard'))

@app.route('/ecole/ajouter_paiement', methods=['POST'])
def ajouter_paiement():
    etablissement_id = session.get('etablissement_id')
    if not etablissement_id:
        abort(403)
        
    ecole = Etablissement.query.get(etablissement_id)
    if ecole.date_expiration < datetime.utcnow() or ecole.statut == 'Suspendu':
        flash("Action bloquée : abonnement inactif.", "danger")
        return redirect(url_for('ecole_dashboard'))
        
    eleve_id = request.form.get('eleve_id')
    montant = float(request.form.get('montant'))
    type_paiement = request.form.get('type_paiement')
    motif = request.form.get('motif')
    
    nouveau_paiement = Paiement(
        montant=montant,
        type_paiement=type_paiement,
        motif=motif,
        eleve_id=eleve_id,
        etablissement_id=etablissement_id,
        enregistre_par_id=session.get('user_id')
    )
    db.session.add(nouveau_paiement)
    db.session.commit()
    
    flash("Le paiement a été encaissé avec succès.", "success")
    return redirect(url_for('ecole_dashboard'))

@app.route('/ecole/ajouter_caissier', methods=['POST'])
def ajouter_caissier():
    etablissement_id = session.get('etablissement_id')
    if not etablissement_id or session.get('user_role') != 'Admin':
        abort(403)
        
    nom = request.form.get('nom')
    email = request.form.get('email')
    password = request.form.get('password')
    
    # Vérification email
    if Utilisateur.query.filter_by(email=email).first():
        flash("Cet email est déjà utilisé par un autre utilisateur.", "danger")
        return redirect(url_for('ecole_dashboard'))
        
    nouveau_caissier = Utilisateur(
        nom=nom,
        email=email,
        mot_de_passe=generate_password_hash(password),
        role='Caissier',
        etablissement_id=etablissement_id
    )
    db.session.add(nouveau_caissier)
    db.session.commit()
    flash(f"Le compte caissier pour {nom} a été créé.", "success")
    return redirect(url_for('ecole_dashboard'))

@app.route('/parent/<code>')
def portail_parent(code):
    eleve = Eleve.query.filter_by(code_parent=code).first_or_404()
    ecole = eleve.etablissement
    
    total_du = eleve.classe_associee.montant_inscription + eleve.classe_associee.montant_scolarite
    total_paye = sum(p.montant for p in eleve.paiements)
    reste_a_payer = total_du - total_paye
    
    return render_template('portail_parent.html', 
                           eleve=eleve, 
                           ecole=ecole, 
                           total_du=total_du, 
                           total_paye=total_paye, 
                           reste_a_payer=reste_a_payer)

@app.route('/ecole/relance/<int:eleve_id>', methods=['POST'])
def relance_email(eleve_id):
    etablissement_id = session.get('etablissement_id')
    if not etablissement_id:
        abort(403)
        
    eleve = Eleve.query.get_or_404(eleve_id)
    if eleve.etablissement_id != etablissement_id:
        abort(403)
        
    if not eleve.email_parent:
        flash("Aucun email parent configuré pour cet élève.", "danger")
        return redirect(url_for('ecole_dashboard'))
        
    total_du = eleve.classe_associee.montant_inscription + eleve.classe_associee.montant_scolarite
    total_paye = sum(p.montant for p in eleve.paiements)
    reste_a_payer = total_du - total_paye
    
    msg = Message(f"Rappel de paiement - {eleve.etablissement.nom}", recipients=[eleve.email_parent])
    msg.body = f"""Bonjour {eleve.nom_parent or 'Madame, Monsieur'},
    
Sauf erreur de notre part, nous constatons qu'il reste un solde de {reste_a_payer} {eleve.etablissement.devise} concernant la scolarité de {eleve.prenom} en classe de {eleve.classe_associee.nom}.

Nous vous remercions de bien vouloir régulariser la situation dans les meilleurs délais.
    
Cordialement,
La Direction de {eleve.etablissement.nom}
"""
    try:
        mail.send(msg)
        flash(f"L'email de relance a été envoyé avec succès à {eleve.email_parent}.", "success")
    except Exception as e:
        flash(f"(Simulation) L'email de relance aurait été envoyé à {eleve.email_parent}. Solde : {reste_a_payer} {eleve.etablissement.devise}.", "info")
        
    return redirect(url_for('ecole_dashboard'))

@app.route('/paiement/<int:paiement_id>/pdf')
def generer_recu_pdf(paiement_id):
    etablissement_id = session.get('etablissement_id')
    if not etablissement_id:
        abort(403)
        
    paiement = Paiement.query.get_or_404(paiement_id)
    if paiement.etablissement_id != etablissement_id:
        abort(403)
        
    eleve = paiement.eleve
    total_du = eleve.classe_associee.montant_inscription + eleve.classe_associee.montant_scolarite
    total_paye = sum(p.montant for p in eleve.paiements)
    reste_a_payer = total_du - total_paye
    
    pdf = FPDF()
    pdf.add_page()
    
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(200, 10, txt=eleve.etablissement.nom.upper(), ln=True, align='C')
    pdf.set_font("Arial", '', 10)
    pdf.cell(200, 10, txt=f"Date d'édition : {datetime.utcnow().strftime('%d/%m/%Y')}", ln=True, align='C')
    
    pdf.ln(20)
    pdf.set_font("Arial", 'B', 14)
    pdf.cell(200, 10, txt=f"RECU DE PAIEMENT N {paiement.id}", ln=True, align='C')
    pdf.ln(10)
    
    pdf.set_font("Arial", '', 12)
    pdf.cell(100, 10, txt=f"Eleve : {eleve.prenom} {eleve.nom}", ln=True)
    pdf.cell(100, 10, txt=f"Classe : {eleve.classe_associee.nom}", ln=True)
    pdf.cell(100, 10, txt=f"Parent : {eleve.nom_parent or 'Non renseigne'}", ln=True)
    
    pdf.ln(10)
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(100, 10, txt=f"Type de reglement : {paiement.type_paiement}", ln=True)
    pdf.cell(100, 10, txt=f"Motif : {paiement.motif}", ln=True)
    pdf.cell(100, 10, txt=f"Date : {paiement.date_paiement.strftime('%d/%m/%Y %H:%M')}", ln=True)
    
    pdf.ln(10)
    pdf.set_font("Arial", 'B', 14)
    pdf.cell(100, 10, txt=f"MONTANT PAYE : {paiement.montant} {eleve.etablissement.devise}", ln=True)
    pdf.set_font("Arial", 'I', 11)
    pdf.cell(100, 10, txt=f"Solde restant du sur l'annee : {reste_a_payer} {eleve.etablissement.devise}", ln=True)
    
    pdf.ln(30)
    pdf.set_font("Arial", 'I', 10)
    pdf.cell(200, 10, txt="La Direction - Document genere par ScolariPay", ln=True, align='C')
    
    pdf_output = pdf.output(dest='S').encode('latin1')
    return send_file(
        io.BytesIO(pdf_output),
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f"Recu_{paiement.id}_{eleve.prenom}.pdf"
    )

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
