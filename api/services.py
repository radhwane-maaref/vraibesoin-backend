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
    Exécute l'extraction des charges récurrentes actives de l'utilisateur sous
    forme de JSON optimisé avec agrégations pour l'analyse IA.
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
    total_fixed = 0.0
    total_var_min = 0.0
    total_var_max = 0.0

    for charge in active_blueprints:
        item = {
            "name": charge['name'],
            "day": charge['due_day'],
            "type": "Fixed" if charge['is_fixed'] else "Variable"
        }

        if charge['is_fixed']:
            amt = float(charge['exact_amount']) if charge['exact_amount'] else 0.0
            item["amt"] = amt
            total_fixed += amt
        else:
            c_min = float(charge['min_amount']) if charge['min_amount'] else 0.0
            c_max = float(charge['max_amount']) if charge['max_amount'] else 0.0
            item["range"] = f"{c_min}-{c_max}"
            total_var_min += c_min
            total_var_max += c_max

        compact_charges.append(item)

    # Wrap the details with a calculated high-level context block
    payload = {
        "summary": {
            "total_fixed_monthly": round(total_fixed, 2),
            "estimated_variable_range": f"{round(total_var_min, 2)}-{round(total_var_max, 2)}"
        },
        "items": compact_charges
    }

    result = json.dumps(payload, ensure_ascii=False)
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

    model_name = 'gemini-3.1-pro-preview'
    img = Image.open(image_file)
    valid_categories = [choice.value for choice in ProductCategoryChoices]
    prompt = f"""
    Agis comme un expert en extraction de données structurées. Extrais les informations de cette image et renvoie-les STRICTEMENT sous forme d'un objet JSON valide.

    [RÈGLES DE TRAITEMENT DU PRIX - TRÈS IMPORTANT]
    - Attention à la devise locale (DT / TND) qui utilise souvent 3 décimales pour les millimes.
    - Si le prix lu sur l'image est "55,000 dt", "55.000" ou "55,000", cela représente 55 dinars. Tu DOIS retourner le nombre décimal 55.0.
    - Ne confonds jamais les 3 zéros des millimes avec des milliers. Réfléchis toujours à la cohérence du prix par rapport au produit.

    [CATÉGORIE]
    - Tu es obligé de choisir une catégorie EXACTE parmi cette liste : {valid_categories}.
    - Si tu hésites ou si rien ne correspond, retourne strictement '{ProductCategoryChoices.OTHER.value}'.

    [FORMAT DE SORTIE (JSON UNIQUEMENT)]
    Exemple attendu :
    {{
        "product_name": "iPhone 15",
        "product_price": 999.0,
        "product_category": "Smartphones"
    }}

    Si une information est introuvable, utilise la valeur null.
    Ne rajoute aucun texte introductif, aucune conclusion, et n'utilise pas de balises Markdown (comme ```json). Le premier caractère de ta réponse doit être {{ et le dernier }}.
    """

    return generate_gemini_json_response(prompt, image_file=img)


def generate_reflection_questions(purchase_id):
    from api.models import PurchaseIntention, ReflectionQuestion
    try:
        intention = PurchaseIntention.objects.select_related('user').get(id=purchase_id)
        user = intention.user
        has_similar = "Oui" if intention.has_similar_item else "Non"

        # Explicitly map behavioral rigor to guide the coach's psychology
        rigor_guidelines = {
            "souple": "Bienveillant, focus sur l'alternative et l'empathie.",
            "modéré": "Équilibré, pose des questions sur l'utilité réelle.",
            "strict": "Direct, sans filtre, confronte l'utilisateur à ses contradictions."
        }
        current_rigor = rigor_guidelines.get(str(user.evaluation_rigor).lower(), "modéré")

        # 1. System Instruction: Dynamic persona definition
        system_instruction = (
            "Tu es un coach financier de poche, expert en psychologie comportementale et neuro-marketing. "
            "Ton but est de stopper les achats impulsifs. Ton style est incisif, percutant et minimaliste. "
            "Tu tutoies l'utilisateur. "
            f"Directives de comportement : {current_rigor}"
        )

        # 2. Prompt: Token-optimized context without wasteful fluff
        prompt = f"""
        Analyse cette intention d'achat et génère exactement 3 questions de réflexion.

        [DONNÉES UTILISATEUR]
        - Produit : {intention.product_name}
        - Prix : {intention.product_price} {user.preferred_currency}
        - Urgence : {intention.urgency_level}/5
        - Possède déjà un équivalent : {has_similar}
        - Fréquence d'utilisation prévue : {intention.usage_frequency or 'Non spécifiée'}

        [RÈGLES DE SÉLECTION]
        - Si Urgence >= 4 : Questionne l'immédiateté (Pourquoi maintenant ?).
        - Si Équivalent == 'Oui' : Confronte sur la redondance (Pourquoi un doublon ?).
        - Si Prix élevé : Demande quel arbitrage financier ou sacrifice cela implique.
        - FORMAT DE DEVISE OBLIGATOIRE : Interdiction stricte d'utiliser le symbole Euro (€). Tu dois obligatoirement écrire le code '{user.preferred_currency}' après le montant (Exemple attendu : '{intention.product_price} {user.preferred_currency}').
        
        Génère 3 questions adaptées à ces règles. Pas d'introduction, pas de conclusion.
        """

        # 3. Schema: Strict token constraints
        schema = {
            "type": "ARRAY",
            "minItems": 3,
            "maxItems": 3,
            "items": {
                "type": "OBJECT",
                "properties": {
                    "question": {
                        "type": "STRING",
                        "description": "Question percutante, max 20 mots. Doit directement utiliser les données utilisateur."
                    },
                    "options": {
                        "type": "ARRAY",
                        "minItems": 3,
                        "maxItems": 3,
                        "items": {"type": "STRING"},
                        "description": "3 réponses types de l'utilisateur (ex: 'Oui, totalement', 'Honnêtement non', 'Je peux attendre'). Max 8 mots par option."
                    }
                },
                "required": ["question", "options"]
            }
        }

        questions_data = generate_gemini_json_response(
            prompt,
            model_name="gemini-3.1-pro-preview",
            response_schema=schema,
            system_instruction=system_instruction
        )


        questions_to_create = [
            ReflectionQuestion(
                purchase_intention=intention,
                question_text=item.get("question"),
                ai_options=item.get("options", [])
            )
            for item in questions_data[:3]
        ]

        return ReflectionQuestion.objects.bulk_create(questions_to_create)

    except Exception as e:
        log_app_error(e, context_message="Erreur génération questions", user=user if 'user' in locals() else None)
        raise e


def generate_ai_verdict(purchase_id):
    try:
        intention = PurchaseIntention.objects.select_related('user').prefetch_related('questions').get(id=purchase_id)
        user = intention.user
        questions = intention.questions.all()

        # Leverages your newly optimized pre-aggregated Redis/JSON utility
        charges_json_context = get_user_active_charges_json(user)

        qna_text = "\n".join([f"- Q: {q.question_text}\n  R: {q.user_answer}" for q in questions])
        goals = ", ".join(user.financial_goals) if user.financial_goals else "Épargne générale"

        # 1. High-impact behavioral persona
        system_instruction = (
            "Tu es un coach financier expérimental, expert en psychologie comportementale. "
            "Ton but est de briser le cycle de l'achat impulsif et du neuro-marketing. "
            "Ton ton est ultra-direct, incisif, analytique mais profondément bienveillant. Tu tutoies l'utilisateur. "
            "Bannis les structures de phrases stéréotypées d'IA (ex: 'En tant que coach...', 'Il est important de...'). "
            "Va droit au but, comme un humain authentique."
            "DEVISE OBLIGATOIRE: Lorsque tu mentionnes un montant ou un prix, utilise TOUJOURS et UNIQUEMENT la devise '{user.preferred_currency}'."
        )

        # 2. Contextually balanced prompt mapping financial space vs. mental space
        prompt = f"""
        Arbitre cette intention d'achat en croisant la réalité des chiffres avec la sincérité psychologique de l'utilisateur.

        [PROFIL FINANCIER DE L'UTILISATEUR]
        - Objectifs prioritaires : {goals}
        - Charges mensuelles (Données brutes + Totaux calculés dans la clé 'summary') : {charges_json_context}

        [L'INTENTION D'ACHAT]
        - Produit cible : {intention.product_name}
        - Coût de l'impulsion : {intention.product_price} {user.preferred_currency}
        
        [CONVERSATION D'AUTO-ÉVALUATION]
        {qna_text}

        [MATRICE DE DÉCISION DU COACH]
        - Choisis 'BUY' si l'achat est mature, aligné aux objectifs et budgétairement indolore.
        - Choisis 'CALM' si l'utilisateur rationalise une impulsion, montre un pic émotionnel ou une hésitation flagrante.
        - Choisis 'ABANDON' si le produit entre en conflit direct avec ses objectifs prioritaires ou ses charges fixes de sécurité.
        """

        # 3. Micro-optimized token schema
        schema = {
            "type": "OBJECT",
            "properties": {
                "verdict": {
                    "type": "STRING",
                    "enum": ["BUY", "CALM", "ABANDON"]
                },
                "explanation": {
                    "type": "STRING",
                    "description": "Analyse psychologique et financière incisive à la première personne ('Je'). Souligne les contradictions directes entre ses réponses et son budget. Max 50 mots."
                },
                "alternatives": {
                    "type": "STRING",
                    "nullable": True,
                    "description": "Une alternative comportementale concrète contre la frustration (ex: règle des 72h, friperie, louer) ou null si le verdict est BUY. Max 15 mots."
                }
            },
            "required": ["verdict", "explanation", "alternatives"]
        }

        # Querying the high-speed flash model execution layer
        result = generate_gemini_json_response(
            prompt,
            model_name="gemini-3.1-pro-preview",
            response_schema=schema,
            system_instruction=system_instruction
        )

        verdict_status = result.get('verdict', 'CALM')
        reasoning = result.get('explanation', '').strip()
        alternative_sugg = result.get('alternatives')

        # Combine text values seamlessly if an alternative exists
        if alternative_sugg and verdict_status != "BUY":
            reasoning += f"\n\n💡 Alternative du coach : {alternative_sugg.strip()}"

        # Direct instance state assignment
        intention.ai_verdict = verdict_status
        intention.ai_reasoning = reasoning
        intention.save()

        return intention

    except Exception as e:
        log_app_error(e, context_message="Erreur generate_ai_verdict", user=user if 'user' in locals() else None)
        raise e


def generate_gemini_json_response(prompt, image_file=None, model_name='gemini-2.5-flash', response_schema=None,
                                  system_instruction=None):
    """
    Exécute une requête vers Gemini avec un schéma de réponse strict pour éliminer la latence de formatage.
    """
    client = genai.Client()
    contents = [prompt, image_file] if image_file else prompt

    # Configure the schema and system instructions
    config_args = {
        "response_mime_type": "application/json",
    }

    if response_schema:
        config_args["response_schema"] = response_schema

    if system_instruction:
        config_args["system_instruction"] = system_instruction

    try:
        response = client.models.generate_content(
            model=model_name,
            contents=contents,
            config=types.GenerateContentConfig(**config_args)
        )
        # No need to strip markdown; the SDK returns raw JSON when a schema is enforced.
        return json.loads(response.text.strip())
    except Exception as e:
        logger.error(f"Erreur API Gemini: {str(e)}")
        raise


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
    return generate_gemini_json_response(prompt,model_name="gemini-3.5-flash")


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
