# api/services.py
import json
import logging
import threading
import requests as std_requests
from PIL import Image
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from google import genai
from google.genai import types
from google.oauth2 import id_token
from api.models import ProductCategoryChoices, PurchaseIntention, RecurringChargeBlueprint
from api.models import ErrorLog
from google.auth.transport import requests as google_requests

logger = logging.getLogger(__name__)


def verify_google_token(token: str) -> dict:
    """Verifies the Google JWT or Access Token and extracts user info."""
    if not token:
        print("❌ verify_google_token: Token is empty")
        return None

    # Check cache first (Fast Redis hit)
    cache_key = f"google_token_{hash(token)}"
    cached_result = cache.get(cache_key)
    if cached_result:
        return cached_result

    try:
        # Check if the token is an Access Token (starts with ya29.)
        if token.startswith('ya29.'):
            response = std_requests.get(
                'https://www.googleapis.com/oauth2/v3/userinfo',
                headers={'Authorization': f'Bearer {token}'}
            )
            if response.status_code == 200:
                data = response.json()
                cache.set(cache_key, data, timeout=300)  # Cache for 5 mins
                return data
            else:
                print(f"❌ verify_google_token Access Token failed: {response.text}")
                logger.error(f"Google Access Token verification failed: {response.text}")
                return None

        # Otherwise, treat it as an ID Token (JWT)
        client_id = settings.GOOGLE_OAUTH_CLIENT_ID
        if client_id:
            client_id = client_id.strip(' "\'')

        # Securely verifies the token signature, expiration, and audience
        idinfo = id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            client_id
        )

        # Returns the decoded JWT payload (e.g., idinfo['email'], idinfo['sub'])
        cache.set(cache_key, idinfo, timeout=300)  # Cache for 5 mins
        return idinfo

    except ValueError as e:
        # Token is invalid, expired, or has the wrong audience
        print(f"❌ verify_google_token failed: {str(e)}")
        logger.error(f"Google token verification failed: {e}")
        return None
    except Exception as e:
        print(f"❌ verify_google_token unexpected error: {str(e)}")
        return None


def send_password_reset_email(email: str, reset_url: str):
    """Sends the reset email via Brevo synchronously (immediate delivery)."""
    subject = "Vrai Besoin - Réinitialisation de votre mot de passe"
    message = f"Bonjour,\n\nVous avez demandé la réinitialisation de votre mot de passe. Cliquez sur le lien suivant : {reset_url}\n\nSi vous n'êtes pas à l'origine de cette demande, ignorez cet e-mail."

    from django.core.mail import send_mail
    send_mail(
        subject=subject,
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        fail_silently=False,
    )


def send_otp_email(email: str, otp_code: str):
    """Envoie l'e-mail de vérification contenant le code OTP de façon synchrone (immédiate)."""
    subject = "Vrai Besoin - Votre code de vérification"
    message = f"Bonjour,\n\nVotre code de vérification à 6 chiffres est : {otp_code}\n\nCe code est valide pendant 10 minutes.\n\nL'équipe Vrai Besoin"

    from django.core.mail import send_mail
    send_mail(
        subject=subject,
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        fail_silently=False,
    )


def get_user_active_charges_json(user) -> str:
    """
    Extracts active recurring charges as an ultra-compact JSON string.
    Optimized to minimize DB hydration overhead and token payload size for LLMs.
    """
    cache_key = f"user_charges_json_{user.id}"
    cached_json = cache.get(cache_key)
    if cached_json:
        return cached_json

    # Bypassing model instance creation entirely using .values() for speed
    active_blueprints = (
        RecurringChargeBlueprint.objects.filter(user=user, is_active=True)
        .order_by('due_day')
        .values('name', 'is_fixed', 'exact_amount', 'min_amount', 'max_amount', 'due_day')
    )

    compact_charges = []
    for charge in active_blueprints:
        item = {
            "name": charge['name'],
            "day": charge['due_day'],
            "type": "Fixed" if charge['is_fixed'] else "Variable"
        }

        # Consolidating amount structural data based on model constraints
        if charge['is_fixed']:
            item["amt"] = float(charge['exact_amount']) if charge['exact_amount'] else 0.0
        else:
            item["range"] = f"{float(charge['min_amount'])}-{float(charge['max_amount'])}"

        compact_charges.append(item)

    # Returns a minimized JSON string ready for prompt injection
    result = json.dumps(compact_charges, ensure_ascii=False)
    cache.set(cache_key, result, timeout=86400)  # Cache for 24h
    return result


def extract_product_data_via_ai(image_file):
    """
    Envoie l'image à l'IA pour extraire le nom, le prix et la catégorie.
    """
    # La clé API doit être stockée de manière sécurisée dans le .env
    client = genai.Client()

    # Utilisation du modèle Flash, optimisé pour les tâches multimodales rapides
    model_name = 'gemini-3.5-flash'
    img = Image.open(image_file)
    valid_categories = [choice.value for choice in ProductCategoryChoices]
    prompt = """
    Extrais les informations suivantes de cette image et renvoie-les STRICTEMENT et UNIQUEMENT sous forme d'un objet JSON valide :
    {"product_name": "iPhone 15", "product_price": 999.00, "product_category": "Smartphones"}
    Pour la categorie, tu es obligé de choisir une parmi la liste suivante : {valid_categories}. Si tu hésites, retourne '{ProductCategoryChoices.OTHER.value}'.
    Si une information est introuvable, mets null. Ne rajoute aucun texte Markdown autour.
    """

    return generate_gemini_json_response(prompt, image_file=img)


def generate_reflection_questions(purchase_id):
    """
    Génère 3 questions de réflexion personnalisées via Gemini.
    Exploite les dimensions d'Utilité et de Psychologie pour cibler les biais cognitifs.
    """
    from api.models import PurchaseIntention, ReflectionQuestion
    try:
        # 1. Récupération des données (Produit + Utilisateur)
        intention = PurchaseIntention.objects.select_related('user').get(id=purchase_id)
        user = intention.user
        charges_json_context = get_user_active_charges_json(user)
        # Formatage booléen pour le prompt
        has_similar = "Oui" if intention.has_similar_item else "Non"

        # 3. Construction du prompt contextuel (Ingénierie de prompt avancée)
        prompt = f"""
        Tu es un coach financier direct et bienveillant. Ton but : éviter les achats impulsifs en posant des questions très simples, compréhensibles par tous.

        [CONTEXTE DE L'ACHAT]
        - Informations nécessaire du produit : {intention.product_name} ({intention.product_category}) | Prix : {intention.product_price}€
        - Niveau d'évaluation : {user.evaluation_rigor}
        - Urgence : {intention.urgency_level}/5 | Déjà possédé : {has_similar} | Utilisation prévue : {intention.usage_frequency or 'Non précisée'}
        - Portefeuille choisie (Financement) : {intention.wallet_type}
        - Categories socio-professionels : {user.socio_professional_categories}
        - Dernière adresse IP de l'utilisateur : {user.last_ip_address}
        - Date de naissance : {user.birth_date}
        - Devise preferé : {user.preferred_currency}
        - CHARGES RÉCURRENTES MENSUELLES DU PROFIL: {charges_json_context}

        [CONSIGNES STRICTES - FORMAT ET STYLE]
        1. QUANTITÉ : Génère EXACTEMENT 3 questions. Pas une de plus.
        2. LONGUEUR : Questions  courtes (MAX 150 caractères). Va droit au but.
        3. OPTIONS : EXACTEMENT 3 options de réponse par question, courtes (MAX 80 caractères).
        4. VOCABULAIRE : Utilise des mots du quotidien. Aucun jargon.
        5. PERTINENCE : Cible le point faible selon le contexte (ex: si l'urgence est forte -> confronte l'émotion ; s'il possède déjà l'objet -> pourquoi changer ? ; si le budget est serré -> rappelle l'objectif).

        RÈGLE ABSOLUE : Renvoie UNIQUEMENT un tableau JSON valide. Aucun texte avant, aucun texte après.
        [
          {{
            "question": "Question courte ici ?",
            "options": ["Choix 1", "Choix 2", "Choix 3"]
          }}
        ]
        """

        questions_data = generate_gemini_json_response(prompt)
        created_questions = []
        for item in questions_data[:3]:
            q = ReflectionQuestion.objects.create(
                purchase_intention=intention,
                question_text=item.get("question"),
                ai_options=item.get("options", [])
            )
            created_questions.append(q)

        return created_questions

    except Exception as e:
        log_app_error(e, context_message="Erreur génération questions", user=user if 'user' in locals() else None)
        raise e


def generate_ai_verdict(purchase_id):
    """
    Service métier optimisé : génère un verdict IA anti-achat impulsif.
    Intègre les données démographiques, contextuelles (IP/Device) et psychologiques.
    """
    try:
        # 1. Chargement optimisé des données
        intention = PurchaseIntention.objects.select_related('user').prefetch_related('questions').get(id=purchase_id)
        user = intention.user
        questions = intention.questions.all()
        charges_json_context = get_user_active_charges_json(user)
        # 2. Enrichissement du contexte Utilisateur (Âge, Profession, Objectifs)
        age = "Non spécifié"
        if user.birth_date:
            age = f"{(timezone.now().date() - user.birth_date).days // 365} ans"

        goals = ", ".join(user.financial_goals) if user.financial_goals else "Épargne générale"
        socio_pro = ", ".join(
            user.socio_professional_categories) if user.socio_professional_categories else "Non spécifiée"


        currency = user.preferred_currency or "€"
        rigor = user.evaluation_rigor or "Équilibré"

        # 3. Enrichissement du contexte Environnemental (IP, Temps, Device)
        # On suppose que le middleware enrichit 'location_data' avec ces infos via l'IP et le User-Agent
        now = timezone.now().strftime("%Y-%m-%d %H:%M")
        city = user.location_data.get('city', 'Localisation inconnue')
        device = user.location_data.get('device_type', 'Mobile/Inconnu')  # Ex: "iPhone", "Mac", "Android"

        # 4. Historique récent (Formatage ultra-compact pour sauver des tokens)
        recent_history = PurchaseIntention.objects.filter(
            user=user, user_final_decision__isnull=False
        ).exclude(id=purchase_id).order_by('-created_at')[:5]

        history_text = ", ".join([f"{item.product_name}({item.user_final_decision})" for item in recent_history])
        if not history_text:
            history_text = "Aucun"

        # 5. Formatage de l'interrogatoire (Q/R)
        qna_text = "\n".join([f"- {q.question_text} : {q.user_answer}" for q in questions])

        has_similar = "Oui" if intention.has_similar_item else "Non"

        # 6. Prompt Engineering Optimisé (Format Instructif Strict)
        prompt = f"""Rôle : Coach financier anti-achat impulsif (Ton: direct, tutoiement, ferme).
Objectif : Rendre un verdict JSON strict pour une intention d'achat.

# CONTEXTE UTILISATEUR
- Age : {age}
- Portefeuille choisie (Financement) : {intention.wallet_type}
- objectif financier : {goals}
- Environnement : {city}, {now}, Appareil : {device}
- Rigueur d'évaluation : {rigor}
- Historique récent : {history_text}
- Categories socio-professionels : {user.socio_professional_categories}
- Devise preferé : {user.preferred_currency}
- CHARGES RÉCURRENTES MENSUELLES DU PROFIL: {charges_json_context}
# ANALYSE DU PRODUIT
- Achat : {intention.product_name} ({intention.product_category})
- Prix : {intention.product_price} {currency}
- Fréquence prévue : {intention.usage_frequency or 'Non spécifiée'}
- Possède déjà un équivalent : {has_similar}
- Urgence psychologique : {intention.urgency_level or 3}/5

# RÉPONSES UTILISATEUR
{qna_text}

# RÈGLES DE DÉCISION
Contexte temporel : Utilise l'heure ({now}) ou l'appareil ({device}) si cela trahit un achat compulsif (ex: achat tard la nuit sur mobile).

# FORMAT DE SORTIE (JSON UNIQUEMENT)
{{
    "verdict": "BUY" | "CALM" | "ABANDON",
    "explanation": "Argumentaire de 3 phrases max. Confronte l'utilisateur avec ses propres réponses.",
    "alternatives": "Suggestion courte (réparation, occasion, location) ou null."
}}"""

        # Appel à l'IA
        result = generate_gemini_json_response(prompt)

        # 7. Post-traitement et Sauvegarde
        reasoning = result.get('explanation', '') or ''
        if result.get('alternatives'):
            reasoning += f"\n\nAlternative suggérée : {result.get('alternatives')}"

        intention.ai_verdict = result.get('verdict', 'CALM').strip()[:10]  # Fallback sur CALM par sécurité
        intention.ai_reasoning = reasoning.strip()
        intention.save()

        return intention

    except Exception as e:
        # Recommandation : importer traceback pour débugger plus facilement
        import traceback
        traceback.print_exc()
        log_app_error(e, context_message="Erreur generate_ai_verdict", user=user if 'user' in locals() else None)
        raise e


def generate_gemini_json_response(prompt, image_file=None, model_name='gemini-2.5-flash'):
    """Utilité pour appeler Gemini API  et nettoyer le output JSON."""
    client = genai.Client()
    contents = [prompt, image_file] if image_file else prompt

    response = client.models.generate_content(
        model=model_name,
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
        )
    )
    clean_text = response.text.replace('```json', '').replace('```', '').strip()
    return json.loads(clean_text)


def log_app_error(exception, context_message="", user=None, endpoint_url=None, level=ErrorLog.LogLevels.ERROR):
    """Utility to standardize error logging across the app."""
    error_message = f"{context_message}: {str(exception)}" if context_message else str(exception)
    ErrorLog.objects.create(
        level=level,
        error_message=error_message,
        endpoint_url=endpoint_url,
        user=user
    )


def fetch_and_cache_daily_advice(user_id, execute_now=False):
    """
    Génère un message de motivation court en arrière-plan via l'API Gemini au moment du login.
    Met en cache le résultat pour le dashboard.
    """
    import datetime
    today = datetime.date.today().isoformat()
    cache_key = f"coach_message_{user_id}_{today}"

    # Vérification si le cache existe déjà (Hit très rapide sur Upstash Redis)
    if cache.get(cache_key):
        return

    # Délégation à Celery (qui utilise Upstash Redis) pour éviter la latence
    if not execute_now:
        from api.tasks import fetch_and_cache_daily_advice_task
        fetch_and_cache_daily_advice_task.delay(user_id)
        return

    from api.models import CustomUser, PurchaseIntention
    from django.db.models import Sum, Count, Q

    try:
        user = CustomUser.objects.get(id=user_id)
        now = timezone.now()

        # Obtenir les stats basiques pour l'IA
        base_qs = PurchaseIntention.objects.filter(user=user)
        if user.history_cleared_at:
            base_qs = base_qs.filter(created_at__gte=user.history_cleared_at)

        stats = base_qs.filter(
            created_at__year=now.year,
            created_at__month=now.month
        ).aggregate(
            monthly_savings=Sum('product_price',
                                filter=Q(user_final_decision=PurchaseIntention.DecisionChoices.ABANDON)),
            total_resolved=Count('id', filter=~Q(user_final_decision__isnull=True) & ~Q(
                user_final_decision=PurchaseIntention.DecisionChoices.UNKOWN)),
            abandoned_intentions=Count('id', filter=Q(user_final_decision=PurchaseIntention.DecisionChoices.ABANDON))
        )

        monthly_savings = float(stats['monthly_savings'] or 0.00)
        total_resolved = stats['total_resolved'] or 0
        abandoned_intentions = stats['abandoned_intentions'] or 0
        mastery_ratio = int((abandoned_intentions / total_resolved) * 100) if total_resolved > 0 else 0

        first_name = user.first_name or "l'utilisateur"
        socio_pro = ", ".join(
            user.socio_professional_categories) if user.socio_professional_categories else "Non spécifiée"
        goals = ", ".join(user.financial_goals) if user.financial_goals else "Épargne générale"
        rigor = user.evaluation_rigor or "Équilibré"
        currency = user.preferred_currency or "€"

        prompt = f"""
        Tu es le Coach IA financier de l'application 'Vrai Besoin'.
        Génère un message d'encouragement TRÈS COURT (1 à 2 phrases maximum) et percutant.

        Contexte de l'utilisateur :
        - Prénom : {first_name}
        - Profil socio-professionnel : {socio_pro}
        - Objectifs financiers (MAX 3) : {goals}
        - Rigueur du coach choisie : {rigor}
        - Économies ce mois-ci : {monthly_savings} {currency}
        - Ratio de maîtrise (impulsions évitées) : {mastery_ratio}%

        Consignes STRICTES :
        - Si les économies sont > 0, félicite-le et rappelle-lui que ça le rapproche de ses objectifs ({goals}).
        - Si les économies sont à 0, motive-le à résister aux achats impulsifs aujourd'hui.
        - Adapte le ton à la rigueur choisie (Indulgent = doux et encourageant, Équilibré = factuel et motivant, Impitoyable = strict, direct et sans filtre).
        - NE mets PAS de guillemets autour du message. NE dis PAS "Bonjour". Rentre directement dans le vif du sujet.
        """

        client = genai.Client()
        response = client.models.generate_content(
            model='gemini-3.5-flash',
            contents=prompt,
        )
        message = response.text.strip()

        # Mise en cache pour 24 heures (86400 secondes)
        cache.set(cache_key, message, timeout=86400)
    except Exception as e:
        log_app_error(e, context_message=f"Erreur génération dynamic coach message pour l'utilisateur {user_id}")


def check_purchase_coherence(product_name, product_category, product_price, preferred_currency):
    """
    Vérifie la cohérence d'une intention d'achat via l'IA.
    Renvoie un dictionnaire avec 'is_coherent' et 'reason'.
    """
    prompt = f"""
    Vérifie la cohérence de cette intention d'achat :
    Nom : "{product_name}"
    Catégorie : "{product_category}"
    Prix : {product_price} {preferred_currency}
    
    Est-ce que ces trois éléments sont logiquement cohérents ensemble dans la réalité ? 
    Réponds STRICTEMENT par un JSON : {{"is_coherent": true/false, "reason": "explication brève"}}
    """
    return generate_gemini_json_response(prompt)


def process_income_payment(income):
    """
    Traite un flux de revenu (met à jour le solde, crée la transaction et calcule la prochaine échéance).
    """
    from django.db import transaction
    from api.models import TransactionHistory
    from datetime import timedelta
    from dateutil.relativedelta import relativedelta
    from django.utils import timezone

    today = timezone.now().date()

    # Tant que le revenu est actif et que la date est arrivée/dépassée
    while income.is_active and (income.next_payment_date is None or income.next_payment_date <= today):
        with transaction.atomic():
            user = income.user

            # 1. Mise à jour du solde
            user.current_balance += income.amount
            user.save(update_fields=['current_balance'])

            # 2. Historisation de la transaction
            TransactionHistory.objects.create(
                user=user,
                amount=income.amount,
                transaction_type=TransactionHistory.TransactionType.INCOME,
                description=f"Revenu perçu : {income.name}"
            )

            # 3. Calcul de la prochaine date selon la fréquence
            if income.frequency == 'ONE_TIME':
                income.is_active = False
            elif income.frequency == 'DAILY':
                income.next_payment_date += timedelta(days=1)
            elif income.frequency == 'WEEKLY':
                income.next_payment_date += timedelta(weeks=1)
            elif income.frequency == 'MONTHLY':
                income.next_payment_date += relativedelta(months=1)
            elif income.frequency == 'YEARLY':
                income.next_payment_date += relativedelta(years=1)

            income.save()
