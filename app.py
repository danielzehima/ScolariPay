from flask import Flask, request, redirect, url_for, session, flash, abort, render_template, send_file
from models import db, Etablissement, Utilisateur, Eleve, Paiement, Classe, Charge
from datetime import datetime, timedelta
import os
import random
import string
import csv
import io
from fpdf import FPDF
from werkzeug.security import generate_password_hash, check_password_hash
from flask_migrate import Migrate
import stripe

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
app.config['SUBSCRIPTION_PRICES'] = {
    'Mensuel': 50000.0,
    'Annuel': 500000.0
}
app.config['STRIPE_SECRET_KEY'] = os.environ.get('STRIPE_SECRET_KEY', 'sk_test_replace_me')
app.config['STRIPE_PUBLIC_KEY'] = os.environ.get('STRIPE_PUBLIC_KEY', 'pk_test_replace_me')
app.config['STRIPE_CURRENCY_MAP'] = {
    'FCFA': 'usd',
    'EUR': 'eur',
    'USD': 'usd',
    'MAD': 'eur',
    'CDF': 'usd',
    'GNF': 'usd'
}
app.config['MOBILE_MONEY_PROVIDERS'] = {
    'Orange Money': {
        'api_key': os.environ.get('ORANGE_MONEY_API_KEY', 'replace_me'),
        'merchant_id': os.environ.get('ORANGE_MONEY_MERCHANT_ID', 'replace_me')
    },
    'MTN Mobile Money': {
        'api_key': os.environ.get('MTN_MOBILE_MONEY_API_KEY', 'replace_me'),
        'merchant_id': os.environ.get('MTN_MOBILE_MONEY_MERCHANT_ID', 'replace_me')
    },
    'Wave': {
        'api_key': os.environ.get('WAVE_API_KEY', 'replace_me'),
        'merchant_id': os.environ.get('WAVE_MERCHANT_ID', 'replace_me')
    },
    'Airtel Money': {
        'api_key': os.environ.get('AIRTEL_MONEY_API_KEY', 'replace_me'),
        'merchant_id': os.environ.get('AIRTEL_MONEY_MERCHANT_ID', 'replace_me')
    }
}
app.config['SUPPORTED_MOBILE_MONEY_PROVIDERS'] = ['Orange Money', 'MTN Mobile Money', 'Wave']

db.init_app(app)
migrate = Migrate(app, db)

# Ne pas oublier d'importer Mail plus haut si possible, sinon on l'importe ici :
from flask_mail import Mail, Message
mail = Mail(app)

def get_subscription_price(type_abonnement):
    return app.config['SUBSCRIPTION_PRICES'].get(type_abonnement, 0.0)


def format_currency(amount):
    try:
        return f"{amount:,.0f}".replace(',', ' ')
    except Exception:
        return str(amount)


def get_stripe_currency(devise):
    return app.config['STRIPE_CURRENCY_MAP'].get(devise.upper(), 'usd')


def get_mobile_money_providers():
    supported = app.config.get('SUPPORTED_MOBILE_MONEY_PROVIDERS', [])
    return [provider for provider in supported if provider in app.config['MOBILE_MONEY_PROVIDERS']]


def is_mobile_money_provider_supported(provider):
    return provider in get_mobile_money_providers()


def mobile_money_provider_configured(provider):
    settings = app.config['MOBILE_MONEY_PROVIDERS'].get(provider, {})
    if not settings:
        return False
    for value in settings.values():
        if not value or 'replace_me' in str(value):
            return False
    return True


def get_mobile_money_provider_settings(provider):
    return app.config['MOBILE_MONEY_PROVIDERS'].get(provider, {})


def simulate_mobile_money_payment(provider, amount, currency, mobile_number, type_abonnement):
    reference = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))
    return {
        'provider': provider,
        'amount': amount,
        'currency': currency,
        'mobile_number': mobile_number,
        'type_abonnement': type_abonnement,
        'reference': reference,
        'status': 'simulated'
    }


def process_mobile_money_payment(provider, amount, currency, mobile_number, type_abonnement):
    if not is_mobile_money_provider_supported(provider):
        raise ValueError(f"Le fournisseur Mobile Money '{provider}' n'est pas supporté actuellement.")

    if not mobile_money_provider_configured(provider):
        raise RuntimeError(f"Les paramètres du fournisseur {provider} ne sont pas configurés.")

    settings = get_mobile_money_provider_settings(provider)
    # Ici on pourrait construire une requête API générique vers le fournisseur.
    # Pour l'instant, on simule le paiement de manière générique.
    simulated = simulate_mobile_money_payment(provider, amount, currency, mobile_number, type_abonnement)
    simulated['settings_used'] = settings
    return simulated


def mobile_money_configured():
    return any(mobile_money_provider_configured(provider) for provider in get_mobile_money_providers())


def stripe_keys_configured():
    secret = app.config.get('STRIPE_SECRET_KEY', '')
    public = app.config.get('STRIPE_PUBLIC_KEY', '')
    return bool(secret and public and 'replace_me' not in secret and 'replace_me' not in public)


def stripe_init():
    if not stripe_keys_configured():
        raise RuntimeError('Stripe keys not configured')
    stripe.api_key = app.config['STRIPE_SECRET_KEY']


def renew_subscription(ecole, type_abonnement):
    reference_date = ecole.date_expiration if ecole.date_expiration and ecole.date_expiration > datetime.utcnow() else datetime.utcnow()
    if type_abonnement == 'Annuel':
        ecole.date_expiration = reference_date + timedelta(days=365)
    else:
        ecole.date_expiration = reference_date + timedelta(days=30)
    ecole.type_abonnement = type_abonnement
    ecole.statut = 'Actif'
    if ecole.est_demo:
        ecole.est_demo = False
    db.session.commit()


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
    return render_template('landing.html',
                          prix_mensuel=get_subscription_price('Mensuel'),
                          prix_annuel=get_subscription_price('Annuel'),
                          devise='FCFA')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        user = Utilisateur.query.filter_by(email=email).first()
        # Vérification sécurisée du mot de passe
        if user:
            if check_password_hash(user.mot_de_passe, password):
                valid_password = True
            elif user.mot_de_passe == password:
                # Ancienne valeur en clair : on upgrade vers un hachage sécurisé
                user.mot_de_passe = generate_password_hash(password)
                db.session.commit()
                valid_password = True
            else:
                valid_password = False

            if valid_password:
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
    
    # Statistiques
    total_mensuel = sum(1 for e in etablissements if e.type_abonnement == 'Mensuel' and e.statut == 'Actif')
    total_annuel = sum(1 for e in etablissements if e.type_abonnement == 'Annuel' and e.statut == 'Actif')
    
    # Notifications (expiration dans <= 7 jours ou déjà expiré)
    aujourdhui = datetime.utcnow()
    limite_notification = aujourdhui + timedelta(days=7)
    ecoles_a_terme = [e for e in etablissements if e.date_expiration <= limite_notification]
    
    # Chart (évolution des abonnés actifs créés récemment)
    chart_labels = []
    chart_data = []
    import json
    dict_ecoles = { (aujourdhui - timedelta(days=i)).date(): 0 for i in range(6, -1, -1)}
    
    for e in etablissements:
        if e.statut == 'Actif' and e.date_creation:
            date_c = e.date_creation.date()
            if date_c in dict_ecoles:
                dict_ecoles[date_c] += 1
                
    for date_jour, count in dict_ecoles.items():
        chart_labels.append(date_jour.strftime('%d/%m'))
        chart_data.append(count)

    return render_template('superadmin_dashboard.html', 
                           etablissements=etablissements,
                           total_mensuel=total_mensuel,
                           total_annuel=total_annuel,
                           ecoles_a_terme=ecoles_a_terme,
                           chart_labels=json.dumps(chart_labels),
                           chart_data=json.dumps(chart_data))

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

@app.route('/superadmin/supprimer_ecole/<int:id>', methods=['POST'])
def supprimer_ecole(id):
    if session.get('user_role') != 'SuperAdmin':
        abort(403)
        
    ecole = Etablissement.query.get_or_404(id)
    nom_ecole = ecole.nom
    
    # Suppression en cascade manuelle
    Paiement.query.filter_by(etablissement_id=ecole.id).delete()
    Charge.query.filter_by(etablissement_id=ecole.id).delete()
    Eleve.query.filter_by(etablissement_id=ecole.id).delete()
    Classe.query.filter_by(etablissement_id=ecole.id).delete()
    Utilisateur.query.filter_by(etablissement_id=ecole.id).delete()
    
    db.session.delete(ecole)
    db.session.commit()
    
    flash(f"L'école {nom_ecole} a été définitivement supprimée.", "success")
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
                           chart_data=json.dumps(chart_data),
                           prix_abonnement_mensuel=get_subscription_price('Mensuel'),
                           prix_abonnement_annuel=get_subscription_price('Annuel'),
                           format_currency=format_currency,
                           stripe_configured=stripe_keys_configured(),
                           mobile_money_providers=get_mobile_money_providers(),
                           mobile_money_configured=mobile_money_configured())

@app.route('/ecole/payment_checkout', methods=['POST'])
def payment_checkout():
    etablissement_id = session.get('etablissement_id')
    if not etablissement_id:
        abort(403)
    if session.get('user_role') != 'Admin':
        flash("Accès refusé : Seul l'administrateur peut renouveler l'abonnement.", "danger")
        return redirect(url_for('ecole_dashboard'))

    type_abonnement = request.form.get('type_abonnement')
    payment_method = request.form.get('payment_method', 'Stripe')
    mobile_provider = request.form.get('mobile_provider')
    mobile_number = request.form.get('mobile_number', '').strip()

    if type_abonnement not in ['Mensuel', 'Annuel']:
        flash("Formule de renouvellement invalide.", "danger")
        return redirect(url_for('ecole_dashboard'))

    ecole = Etablissement.query.get_or_404(etablissement_id)
    amount = int(get_subscription_price(type_abonnement) * 100)
    currency = get_stripe_currency(ecole.devise)

    if payment_method == 'Mobile Money':
        if not mobile_provider:
            flash("Veuillez choisir un fournisseur Mobile Money.", "danger")
            return redirect(url_for('ecole_dashboard'))

        if not mobile_number:
            flash("Veuillez renseigner le numéro Mobile Money du payeur.", "danger")
            return redirect(url_for('ecole_dashboard'))

        if not is_mobile_money_provider_supported(mobile_provider):
            flash(f"Le fournisseur {mobile_provider} n'est pas supporté. Choisissez Orange Money, MTN Mobile Money ou Wave.", "danger")
            return redirect(url_for('ecole_dashboard'))

        if not mobile_money_provider_configured(mobile_provider):
            renew_subscription(ecole, type_abonnement)
            flash(f"Mobile Money ({mobile_provider}) non configuré : abonnement {type_abonnement} simulé localement pour {format_currency(get_subscription_price(type_abonnement))} {ecole.devise}.", "info")
            return redirect(url_for('ecole_dashboard'))

        try:
            result = process_mobile_money_payment(mobile_provider, amount, currency, mobile_number, type_abonnement)
            renew_subscription(ecole, type_abonnement)
            flash(
                f"Paiement Mobile Money ({mobile_provider}) simulé : {format_currency(amount/100)} {currency} vers {mobile_number}. Référence {result['reference']}.",
                "success"
            )
        except Exception as e:
            flash(f"Erreur Mobile Money : {str(e)}", "danger")
        return redirect(url_for('ecole_dashboard'))

    # Stripe par défaut
    if not stripe_keys_configured():
        renew_subscription(ecole, type_abonnement)
        flash(f"Stripe non configuré : abonnement {type_abonnement} simulé localement pour {format_currency(get_subscription_price(type_abonnement))} {ecole.devise}.", "info")
        return redirect(url_for('ecole_dashboard'))

    stripe_init()
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': currency,
                    'product_data': {
                        'name': f"Renouvellement {type_abonnement}",
                        'description': f"{type_abonnement} - {format_currency(get_subscription_price(type_abonnement))} {ecole.devise}"
                    },
                    'unit_amount': amount
                },
                'quantity': 1
            }],
            mode='payment',
            success_url=url_for('stripe_success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=url_for('ecole_dashboard', _external=True),
            metadata={
                'ecole_id': str(ecole.id),
                'type_abonnement': type_abonnement
            }
        )
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        flash(f"Impossible de démarrer le paiement Stripe : {str(e)}", "danger")
        return redirect(url_for('ecole_dashboard'))


@app.route('/ecole/stripe_success')
def stripe_success():
    session_id = request.args.get('session_id')
    if not session_id:
        flash("Session Stripe manquante.", "danger")
        return redirect(url_for('ecole_dashboard'))

    stripe_init()
    try:
        stripe_session = stripe.checkout.Session.retrieve(session_id)
    except Exception as e:
        flash(f"Impossible de vérifier le paiement Stripe : {str(e)}", "danger")
        return redirect(url_for('ecole_dashboard'))

    if stripe_session.payment_status != 'paid':
        flash("Paiement non confirmé. Veuillez réessayer.", "warning")
        return redirect(url_for('ecole_dashboard'))

    metadata = stripe_session.metadata or {}
    if metadata.get('ecole_id') is None:
        flash("Métadonnées Stripe manquantes.", "danger")
        return redirect(url_for('ecole_dashboard'))

    ecole = Etablissement.query.get(int(metadata['ecole_id']))
    if not ecole or ecole.id != session.get('etablissement_id'):
        abort(403)

    type_abonnement = metadata.get('type_abonnement', 'Mensuel')
    reference_date = ecole.date_expiration if ecole.date_expiration and ecole.date_expiration > datetime.utcnow() else datetime.utcnow()
    if type_abonnement == 'Annuel':
        ecole.date_expiration = reference_date + timedelta(days=365)
    else:
        ecole.date_expiration = reference_date + timedelta(days=30)

    ecole.type_abonnement = type_abonnement
    ecole.statut = 'Actif'
    if ecole.est_demo:
        ecole.est_demo = False
    db.session.commit()

    flash(f"Paiement Stripe confirmé : abonnement {type_abonnement} renouvelé.", "success")
    return redirect(url_for('ecole_dashboard'))


@app.route('/ecole/renouveler_abonnement', methods=['POST'])
def renouveler_abonnement():
    etablissement_id = session.get('etablissement_id')
    if not etablissement_id:
        abort(403)
    if session.get('user_role') != 'Admin':
        flash("Accès refusé : Seul l'administrateur peut renouveler l'abonnement.", "danger")
        return redirect(url_for('ecole_dashboard'))

    ecole = Etablissement.query.get_or_404(etablissement_id)
    nouveau_type = request.form.get('type_abonnement')
    if nouveau_type not in ['Mensuel', 'Annuel']:
        flash("Type d'abonnement invalide.", "danger")
        return redirect(url_for('ecole_dashboard'))

    prix = get_subscription_price(nouveau_type)
    ecole.type_abonnement = nouveau_type
    reference_date = ecole.date_expiration if ecole.date_expiration and ecole.date_expiration > datetime.utcnow() else datetime.utcnow()
    if nouveau_type == 'Annuel':
        ecole.date_expiration = reference_date + timedelta(days=365)
    else:
        ecole.date_expiration = reference_date + timedelta(days=30)

    ecole.statut = 'Actif'
    if ecole.est_demo:
        ecole.est_demo = False

    db.session.commit()
    flash(f"Abonnement renouvelé avec succès pour {'12 mois' if nouveau_type == 'Annuel' else '30 jours'} au tarif de {format_currency(prix)} {ecole.devise}.", "success")
    return redirect(url_for('ecole_dashboard'))

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
        mot_de_passe=generate_password_hash(password),
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

@app.route('/signup_with_payment', methods=['POST'])
def signup_with_payment():
    nom_ecole = request.form.get('nom_ecole', '').strip()
    email_contact = request.form.get('email_contact', '').strip()
    plan = request.form.get('plan', 'Mensuel')
    payment_method = request.form.get('payment_method', 'Stripe')
    mobile_provider = request.form.get('mobile_provider', '')
    mobile_number = request.form.get('mobile_number', '').strip()

    if not nom_ecole or not email_contact:
        flash("Veuillez remplir le nom de l'établissement et l'email.", "danger")
        return redirect(url_for('index'))

    if plan not in ['Mensuel', 'Annuel']:
        flash("Plan invalide.", "danger")
        return redirect(url_for('index'))

    # Vérifier que l'email n'existe pas déjà
    existing = Etablissement.query.filter_by(email=email_contact).first()
    if existing:
        flash("Cet email est déjà utilisé. Veuillez vous connecter.", "info")
        return redirect(url_for('login'))

    try:
        # Créer le nouvel établissement
        nouvelle_ecole = Etablissement(
            nom=nom_ecole,
            email=email_contact,
            type_abonnement=plan,
            date_expiration=datetime.utcnow() + (timedelta(days=365) if plan == 'Annuel' else timedelta(days=30)),
            statut='Actif',
            est_demo=False,
            date_creation=datetime.utcnow(),
            devise='FCFA'
        )
        db.session.add(nouvelle_ecole)
        db.session.flush()

        # Générer un mot de passe temporaire
        temp_password = ''.join(random.choices(string.ascii_letters + string.digits, k=12))

        # Créer l'utilisateur administrateur
        nouvel_admin = Utilisateur(
            nom=f"Directeur de {nom_ecole}",
            email=email_contact,
            mot_de_passe=generate_password_hash(temp_password),
            role='Admin',
            etablissement_id=nouvelle_ecole.id
        )
        db.session.add(nouvel_admin)
        db.session.commit()

        # Créer la session
        session['user_id'] = nouvel_admin.id
        session['user_role'] = nouvel_admin.role
        session['etablissement_id'] = nouvelle_ecole.id

        # Afficher les détails du paiement
        if payment_method == 'Mobile Money':
            if not is_mobile_money_provider_supported(mobile_provider):
                flash(f"Le fournisseur {mobile_provider} n'est pas supporté.", "danger")
                return redirect(url_for('ecole_dashboard'))

            if not mobile_money_provider_configured(mobile_provider):
                flash(f"Inscription créée ! Mobile Money ({mobile_provider}) n'est pas configuré - paiement simulé localement.", "info")
            else:
                try:
                    amount = int(get_subscription_price(plan) * 100)
                    currency = get_stripe_currency(nouvelle_ecole.devise)
                    result = process_mobile_money_payment(mobile_provider, amount, currency, mobile_number, plan)
                    flash(f"Inscription créée ! Paiement Mobile Money ({mobile_provider}) traité : Référence {result['reference']}.", "success")
                except Exception as e:
                    flash(f"Inscription créée ! Erreur Mobile Money : {str(e)}", "warning")
        else:
            # Stripe
            if not stripe_keys_configured():
                flash(f"Inscription créée ! Stripe n'est pas configuré - paiement simulé localement.", "info")
            else:
                flash(f"Inscription créée ! Vous pouvez à présent renouveler votre abonnement via Stripe.", "success")

        return redirect(url_for('ecole_dashboard'))

    except Exception as e:
        db.session.rollback()
        flash(f"Erreur lors de la création du compte : {str(e)}", "danger")
        return redirect(url_for('index'))

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
                mot_de_passe=generate_password_hash('admin'),
                role='SuperAdmin'
            )
            db.session.add(superadmin)
            db.session.commit()
            print("Compte SuperAdmin créé : admin@saas.com / admin")
            
    app.run(debug=True, port=5000)
