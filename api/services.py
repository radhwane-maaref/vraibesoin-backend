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
    """
    Vérifie le jeton d'accès ou JWT fourni par Google et extrait les informations utilisateur.

    Args:
        token (str): Le jeton Google (Access Token ou ID Token/JWT).

    Returns:
        dict: Les données extraites du jeton si la vérification réussit, sinon None.
    """
    if not token:
        print("❌ verify_google_token: Token is empty")
        return None

    cache_key = f"google_token_{hash(token)}"
    cached_result = cache.get(cache_key)
    if cached_result:
        return cached_result

    try:
        if token.startswith('ya29.'):
            response = std_requests.get(
                'https://www.googleapis.com/oauth2/v3/userinfo',
                headers={'Authorization': f'Bearer {token}'}
            )
            if response.status_code == 200:
                data = response.json()
                cache.set(cache_key, data, timeout=300)
                return data
            else:
                print(f"❌ verify_google_token Access Token failed: {response.text}")
                logger.error(f"Google Access Token verification failed: {response.text}")
                return None

        client_id = settings.GOOGLE_OAUTH_CLIENT_ID
        if client_id:
            client_id = client_id.strip(' "\'')

        idinfo = id_token.verify_oauth2_token(
            token,
            google_requests.Request(),
            client_id
        )

        cache.set(cache_key, idinfo, timeout=300)
        return idinfo

    except ValueError as e:
        print(f"❌ verify_google_token failed: {str(e)}")
        logger.error(f"Google token verification failed: {e}")
        return None
    except Exception as e:
        print(f"❌ verify_google_token unexpected error: {str(e)}")
        return None


def send_password_reset_email(email: str, reset_url: str):
    """
    Envoie un e-mail contenant le lien de réinitialisation du mot de passe.

    Args:
        email (str): L'adresse e-mail de destination.
        reset_url (str): L'URL permettant de réinitialiser le mot de passe.

    Returns:
        None
    """
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
    """
    Envoie un e-mail contenant le code de vérification OTP (One-Time Password).

    Args:
        email (str): L'adresse e-mail de destination.
        otp_code (str): Le code OTP à usage unique.

    Returns:
        None
    """
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
    Exécute l'extraction des charges récurrentes actives de l'utilisateur sous forme de JSON optimisé.

    Args:
        user (CustomUser): L'instance de l'utilisateur.

    Returns:
        str: Une chaîne JSON compacte listant les charges actives de l'utilisateur.
    """
    cache_key = f"user_charges_json_{user.id}"
    cached_json = cache.get(cache_key)
    if cached_json:
        return cached_json

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

        if charge['is_fixed']:
            item["amt"] = float(charge['exact_amount']) if charge['exact_amount'] else 0.0
        else:
            item["range"] = f"{float(charge['min_amount'])}-{float(charge['max_amount'])}"

        compact_charges.append(item)

    result = json.dumps(compact_charges, ensure_ascii=False)
    cache.set(cache_key, result, timeout=86400)
    return result


def extract_product_data_via_ai(image_file):
    """
    Sollicite le modèle d'intelligence artificielle pour extraire les métadonnées d'un produit depuis une image.

    Args:
        image_file (UploadedFile or file-like object): L'image représentant le produit à analyser.

    Returns:
        dict: Un dictionnaire JSON contenant le nom, le prix et la catégorie extraits.
    """
    client = genai.Client()

    model_name = 'gemini-3.1-flash-lite'
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
    Génère un ensemble de questions de réflexion ciblées en fonction de l'intention d'achat.

    Args:
        purchase_id (UUID): L'identifiant de l'intention d'achat ciblée.

    Returns:
        list[ReflectionQuestion]: La liste des objets ReflectionQuestion créés en base de données.

    Raises:
        Exception: Remonte toute exception survenant durant la génération ou la sauvegarde.
    """
    from api.models import PurchaseIntention, ReflectionQuestion
    try:
        intention = PurchaseIntention.objects.select_related('user').get(id=purchase_id)
        user = intention.user
        charges_json_context = get_user_active_charges_json(user)
        has_similar = "Oui" if intention.has_similar_item else "Non"

        prompt = f"""
        Tu es un coach financier direct et bienveillant. Ton but : éviter les achats impulsifs en posant des questions très simples, compréhensibles par tous.

        [CONTEXTE DE L'ACHAT]
        - Informations nécessaire du produit : {intention.product_name} ({intention.product_category}) | Prix : {intention.product_price}{user.preferred_currency}
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

        questions_data = generate_gemini_json_response(prompt,model_name="gemini-3.5-flash")
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
    Analyse l'intention d'achat et produit un verdict consultatif anti-impulsion.

    Évalue le profil, les finances et le contexte pour recommander l'achat, la réflexion
    ou l'abandon, tout en proposant d'éventuelles alternatives.

    Args:
        purchase_id (UUID): L'identifiant de l'intention d'achat.

    Returns:
        PurchaseIntention: L'instance de l'intention d'achat mise à jour avec le verdict.

    Raises:
        Exception: Remonte toute exception liée à l'interfaçage LLM ou la sauvegarde.
    """
    try:
        intention = PurchaseIntention.objects.select_related('user').prefetch_related('questions').get(id=purchase_id)
        user = intention.user
        questions = intention.questions.all()
        charges_json_context = get_user_active_charges_json(user)

        age = "Non spécifié"
        if user.birth_date:
            age = f"{(timezone.now().date() - user.birth_date).days // 365} ans"

        goals = ", ".join(user.financial_goals) if user.financial_goals else "Épargne générale"
        socio_pro = ", ".join(
            user.socio_professional_categories) if user.socio_professional_categories else "Non spécifiée"

        currency = user.preferred_currency or "€"
        rigor = user.evaluation_rigor or "Équilibré"

        now = timezone.now().strftime("%Y-%m-%d %H:%M")
        city = user.location_data.get('city', 'Localisation inconnue')
        device = user.location_data.get('device_type', 'Mobile/Inconnu')

        recent_history = PurchaseIntention.objects.filter(
            user=user, user_final_decision__isnull=False
        ).exclude(id=purchase_id).order_by('-created_at')[:5]

        history_text = ", ".join([f"{item.product_name}({item.user_final_decision})" for item in recent_history])
        if not history_text:
            history_text = "Aucun"

        qna_text = "\n".join([f"- {q.question_text} : {q.user_answer}" for q in questions])

        has_similar = "Oui" if intention.has_similar_item else "Non"

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

        result = generate_gemini_json_response(prompt,model_name="gemini-3.5-flash")

        reasoning = result.get('explanation', '') or ''
        if result.get('alternatives'):
            reasoning += f"\n\nAlternative suggérée : {result.get('alternatives')}"

        intention.ai_verdict = result.get('verdict', 'CALM').strip()[:10]
        intention.ai_reasoning = reasoning.strip()
        intention.save()

        return intention

    except Exception as e:
        import traceback
        traceback.print_exc()
        log_app_error(e, context_message="Erreur generate_ai_verdict", user=user if 'user' in locals() else None)
        raise e


def generate_gemini_json_response(prompt, image_file=None, model_name='gemini-2.5-flash'):
    """
    Exécute une requête vers le modèle d'intelligence artificielle Gemini en forçant une sortie JSON.

    Args:
        prompt (str): L'invite textuelle définissant le contexte et la requête.
        image_file (UploadedFile or None, optional): Fichier image optionnel. Defaults to None.
        model_name (str, optional): Modèle Gemini ciblé. Defaults to 'gemini-2.5-flash'.

    Returns:
        dict: Le dictionnaire JSON contenant la réponse du modèle.
    """
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
    """
    Standardise et persiste l'enregistrement d'une erreur applicative en base de données.

    Args:
        exception (Exception): L'exception source capturée.
        context_message (str, optional): Message de contexte supplémentaire. Defaults to "".
        user (CustomUser, optional): Utilisateur concerné, le cas échéant. Defaults to None.
        endpoint_url (str, optional): URL associée à l'erreur. Defaults to None.
        level (str, optional): Niveau de criticité de l'erreur. Defaults to ErrorLog.LogLevels.ERROR.

    Returns:
        None
    """
    error_message = f"{context_message}: {str(exception)}" if context_message else str(exception)
    ErrorLog.objects.create(
        level=level,
        error_message=error_message,
        endpoint_url=endpoint_url,
        user=user
    )


def fetch_and_cache_daily_advice(user_id, execute_now=False):
    """
    Génère et stocke en cache un conseil ou encouragement quotidien personnalisé pour l'utilisateur.

    Args:
        user_id (int): L'identifiant de l'utilisateur.
        execute_now (bool, optional): Force l'exécution synchrone sans recourir à Celery. Defaults to False.

    Returns:
        None
    """
    import datetime
    today = datetime.date.today().isoformat()
    cache_key = f"coach_message_{user_id}_{today}"

    if cache.get(cache_key):
        return

    if not execute_now:
        from api.tasks import fetch_and_cache_daily_advice_task
        fetch_and_cache_daily_advice_task.delay(user_id)
        return

    from api.models import CustomUser, PurchaseIntention
    from django.db.models import Sum, Count, Q

    try:
        user = CustomUser.objects.get(id=user_id)
        now = timezone.now()

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
            model='gemini-2.5-flash',
            contents=prompt,
        )
        message = response.text.strip()

        cache.set(cache_key, message, timeout=86400)
    except Exception as e:
        log_app_error(e, context_message=f"Erreur génération dynamic coach message pour l'utilisateur {user_id}")


def check_purchase_coherence(product_name, product_category, product_price, preferred_currency):
    """
    Exécute une vérification contextuelle pour détecter les saisies incohérentes 
    d'une intention d'achat.

    Args:
        product_name (str): Le nom du produit souhaité.
        product_category (str): La catégorie déclarée pour ce produit.
        product_price (float or Decimal): Le prix du produit.
        preferred_currency (str): La devise de tarification.

    Returns:
        dict: Un dictionnaire contenant les clés 'is_coherent' (booléen) et 'reason' (explication texte).
    """
    prompt = prompt = f"""
    Tu es un expert en évaluation de données pour une application de finances personnelles. 
    Ton rôle est d'identifier les erreurs de saisie, les fautes de frappe ou les incohérences flagrantes dans les intentions d'achat des utilisateurs.
    Analyse l'intention d'achat suivante :
    - Nom du produit : "{product_name}"
    - Catégorie : "{product_category}"
    - Prix saisi : {product_price} {preferred_currency}
    Évalue la cohérence globale selon ces deux critères stricts :
    1. Pertinence de la catégorie : La catégorie "{product_category}" correspond-elle logiquement à la nature du produit "{product_name}" ?
    2. Ordre de grandeur du prix : Le prix de {product_price} {preferred_currency} est-il réaliste sur le marché actuel ? (Prends en compte le marché de l'occasion et les promotions, mais rejette les aberrations manifestes comme un smartphone récent à 10% de sa valeur réelle ou un article de tous les jours à un prix exorbitant).

    Fournis ta réponse STRICTEMENT au format JSON exact suivant, sans aucun formatage Markdown ni texte avant ou après :
    {{
        "reason": "Analyse d'abord le prix par rapport au marché et la pertinence de la catégorie en une phrase concise.",
        "is_coherent": true ou false
    }}
    """
    return generate_gemini_json_response(prompt,model_name="gemini-3.1-flash-lite")


def process_income_payment(income):
    """
    Exécute le traitement d'encaissement et de mise à jour pour un flux de revenus donné.

    Met à jour le solde utilisateur, enregistre la transaction historique et décale 
    automatiquement la date d'échéance selon la périodicité du revenu.

    Args:
        income (IncomeStream): L'instance du flux de revenus à traiter.

    Returns:
        None
    """
    from django.db import transaction
    from api.models import TransactionHistory
    from datetime import timedelta
    from dateutil.relativedelta import relativedelta
    from django.utils import timezone

    today = timezone.now().date()

    while income.is_active and (income.next_payment_date is None or income.next_payment_date <= today):
        with transaction.atomic():
            user = income.user

            user.current_balance += income.amount
            user.save(update_fields=['current_balance'])

            TransactionHistory.objects.create(
                user=user,
                amount=income.amount,
                transaction_type=TransactionHistory.TransactionType.INCOME,
                description=f"Revenu perçu : {income.name}"
            )

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
