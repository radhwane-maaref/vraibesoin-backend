# api/services.py
import json
import threading
from PIL import Image
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone
from google import genai
from google.auth.transport import requests
from google.genai import types
from google.oauth2 import id_token
from api.models import ProductCategoryChoices
from api.models import ErrorLog
from django.core.cache import cache

def verify_google_token(token: str) -> dict:
    """Verifies the Google JWT and extracts user info."""
    try:
        # Client ID should be loaded from .env via settings
        client_id = settings.GOOGLE_OAUTH_CLIENT_ID
        idinfo = id_token.verify_oauth2_token(token, requests.Request(), client_id)
        return idinfo
    except ValueError:
        return None


def send_password_reset_email(email: str, reset_url: str):
    """Sends the reset email via Brevo (configured as SMTP in settings.py)."""
    subject = "Vrai Besoin - Réinitialisation de votre mot de passe"
    message = f"Bonjour,\n\nVous avez demandé la réinitialisation de votre mot de passe. Cliquez sur le lien suivant : {reset_url}\n\nSi vous n'êtes pas à l'origine de cette demande, ignorez cet e-mail."

    send_mail(
        subject=subject,
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        fail_silently=False,
    )
    threading.Thread(target=send_mail, args=(reset_url,)).start()


def send_otp_email(email: str, otp_code: str):
    """Envoie l'e-mail de vérification contenant le code OTP."""
    subject = "Vrai Besoin - Votre code de vérification"
    message = f"Bonjour,\n\nVotre code de vérification à 6 chiffres est : {otp_code}\n\nCe code est valide pendant 10 minutes.\n\nL'équipe Vrai Besoin"

    # Utilisation d'un thread pour envoyer l'e-mail de manière asynchrone
    threading.Thread(
        target=send_mail,
        args=(subject, message, settings.DEFAULT_FROM_EMAIL, [email], False)
    ).start()


def extract_product_data_via_ai(image_file):
    """
    Envoie l'image à l'IA pour extraire le nom, le prix et la catégorie.
    """
    # La clé API doit être stockée de manière sécurisée dans le .env
    client = genai.Client()

    # Utilisation du modèle Flash, optimisé pour les tâches multimodales rapides
    model_name = 'gemini-3.1-flash-lite-preview'
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

        # Formatage booléen pour le prompt
        has_similar = "Oui" if intention.has_similar_item else "Non"

        # 3. Construction du prompt contextuel (Ingénierie de prompt avancée)
        prompt = f"""
        Tu es un coach financier strict mais empathique luttant contre l'achat compulsif. 
        Un utilisateur veut acheter : "{intention.product_name}" ({intention.product_category}) pour {intention.product_price}€.

        [PROFIL UTILISATEUR]
        - Profession : {user.profession or 'Non spécifiée'}
        - Budget mensuel : {user.monthly_budget or 'Non spécifié'}€
        - Objectif financier : {user.financial_goal or 'Épargner'}

        [ANALYSE DU BESOIN DÉCLARÉ (TRÈS IMPORTANT)]
        - Fréquence d'utilisation prévue : {intention.usage_frequency or 'Non spécifiée'}
        - Possède déjà un objet similaire : {has_similar}
        - Niveau d'urgence psychologique : {intention.urgency_level or 3}/5 (1 = Peut attendre, 5 = Besoin immédiat)

        Ta mission : Génère exactement 3 questions pour le faire réfléchir.
        Pour chaque question, fournis 3 à  5 options de réponses réalistes et pertinentes par rapport au contexte.
        STRATÉGIE DE COACHING : 
        - S'il possède déjà un objet similaire, questionne pourquoi l'ancien ne suffit plus.
        - Si l'urgence est à 4 ou 5, questionne si cette urgence est réelle ou dictée par une émotion/promotion.
        - Si la fréquence d'utilisation est faible ("Rarement"), questionne la rentabilité de l'achat ou l'option de location.

        RÈGLE ABSOLUE : [
          {{
                "question": "Texte de la question 1",
                "options": ["Option 1", "Option 2", "Option 3"]
          }},...]"""

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
    Service métier qui rassemble le contexte, interroge le LLM et structure la réponse en JSON.
    Applique la décision finale basée sur l'historique et les biais psychologiques détectés.
    """
    from api.models import PurchaseIntention
    try:
        # 1. Chargement des données avec optimisation SQL
        intention = PurchaseIntention.objects.select_related('user').prefetch_related('questions').get(id=purchase_id)
        user = intention.user
        questions = intention.questions.all()

        # Formatage des variables
        now = timezone.now().strftime("%Y-%m-%d %H:%M")
        has_similar = "Oui" if intention.has_similar_item else "Non"

        # 2. Historique récent
        recent_history = PurchaseIntention.objects.filter(
            user=user,
            user_final_decision__isnull=False
        ).exclude(id=purchase_id).order_by('-created_at')[:5]

        history_text = ", ".join([f"{item.product_name} ({item.user_final_decision})" for item in recent_history])
        if not history_text:
            history_text = "Aucun historique récent."

        # 3. Formatage de l'interrogatoire
        qna_text = "\n".join([f"Q: {q.question_text} | R: {q.user_answer}" for q in questions])
        rigor_instruction = user.evaluation_rigor or 'Équilibré'
        # 5. Ingénierie du Prompt (Le "Cerveau" du Coach)
        prompt = f"""
        Tu es "Vrai Besoin", un coach financier anti-achat impulsif. Rends ton verdict final de manière ferme et argumentée.

        [CONTEXTE TEMPOREL ET LOCAL]
        - Date et heure : {now}
        - Localisation (IP) : {user.location_data.get('city', 'Inconnue')}

        [PROFIL FINANCIER ET COMPORTEMENTAL]
        - Budget mensuel : {user.monthly_budget or 'Non défini'}€
        - Objectif principal : {user.financial_goal or 'Épargne générale'}
        - Comportement récent : {history_text}

        [DIMENSIONS DU PRODUIT ET DU BESOIN]
        - Nom : "{intention.product_name}" ({intention.product_category})
        - Prix : {intention.product_price}€
        - Fréquence d'utilisation prévue : {intention.usage_frequency or 'Non spécifiée'}
        - Possède déjà un objet similaire : {has_similar}
        - Niveau d'urgence psychologique déclaré : {intention.urgency_level or 3}/5

        [INTERROGATOIRE]
        {qna_text}

        STRATÉGIE DE VERDICT (RÈGLES DE DÉCISION) :
        - {rigor_instruction}
        - Déconstruis les justifications de l'utilisateur. Si l'urgence est à 5 mais qu'il possède déjà un objet similaire, c'est presque toujours une pulsion ("FOMO" - Fear Of Missing Out).
        - Si la fréquence d'utilisation est "Rarement", l'achat n'est pas justifié financièrement. Recommande l'abandon ou l'attente.

        RÈGLE ABSOLUE : Tu dois impérativement renvoyer un objet JSON strict.
        Attention : Pour la clé "verdict", utilise EXACTEMENT les codes de base de données suivants : "BUY" (pour acheter), "CALM" (pour attendre), ou "ABANDON" (pour abandonner).

        {{
            "verdict": "BUY" | "CALM" | "ABANDON",
            "explanation": "Argumentaire de 3 phrases maximum, tutoiement, ton de coach direct. Utilise ses réponses et son niveau d'urgence pour le confronter.",
            "alternatives": "Une suggestion d'alternative (réparation, location, gratuité)."
        }}
        """

        result = generate_gemini_json_response(prompt)

        # 7. Sauvegarde sécurisée
        reasoning = result.get('explanation', '') or ''
        if result.get('alternatives'):
            reasoning += f"\n\nAlternative suggérée : {result.get('alternatives')}"

        intention.ai_verdict = result.get('verdict', '').strip()[:10]
        intention.ai_reasoning = reasoning.strip()
        intention.save()

        return intention

    except Exception as e:
        log_app_error(e, context_message="Erreur generate_ai_verdict", user=user if 'user' in locals() else None)
        raise e


def generate_gemini_json_response(prompt, image_file=None):
    """Utilité pour appeler Gemini API  et nettoyer le output JSON."""
    client = genai.Client()
    contents = [prompt, image_file] if image_file else prompt

    response = client.models.generate_content(
        model='gemini-3.1-flash-lite-preview',
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
