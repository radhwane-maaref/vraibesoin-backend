# api/views.py
from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status, generics
from rest_framework.exceptions import NotFound
from rest_framework.pagination import PageNumberPagination
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import authenticate
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from rest_framework.permissions import IsAuthenticated
from .serializers import (
    CustomUserSerializer,
    SetNewPasswordSerializer,
    ResetPasswordEmailRequestSerializer,
    UserRegistrationSerializer,
    ProductImageExtractionSerializer, PurchaseIntentionSerializer, ReflectionQuestionSerializer,
    FinalDecisionUpdateSerializer, AppFeedbackSerializer, AdminFeedbackSerializer, ErrorLogSerializer,
    AdminUserListSerializer, OnboardingSerializer
)
from .models import CustomUser, ErrorLog, ReflectionQuestion, SavingsGoal, AppFeedback, \
    ProductCategoryChoices, BudgetChoices, SocioProChoices
from .services import verify_google_token, send_password_reset_email, generate_ai_verdict, extract_product_data_via_ai, \
    generate_reflection_questions, generate_gemini_json_response, log_app_error
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from django.utils import timezone
from datetime import timedelta
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from rest_framework.throttling import AnonRateThrottle
from django.core.cache import cache
import random
from .services import send_otp_email


class PasswordResetAnonThrottle(AnonRateThrottle):
    scope = 'password_reset'


def get_user_tokens(user):
    """Helper to generate JWTs for the Vue.js frontend."""
    refresh = RefreshToken.for_user(user)
    return {
        'refresh': str(refresh),
        'access': str(refresh.access_token),
    }


class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get('email')
        otp_submitted = request.data.get('otp')

        # Bloquer si pas d'OTP
        if not otp_submitted:
            return Response({'error': 'Le code OTP est requis.'}, status=status.HTTP_400_BAD_REQUEST)

        # Vérifier si l'OTP correspond à celui en cache
        cached_otp = cache.get(f"otp_{email}")
        if not cached_otp or str(cached_otp) != str(otp_submitted):
            return Response({'error': 'Code OTP invalide ou expiré.'}, status=status.HTTP_400_BAD_REQUEST)

        # Continuer avec l'inscription normale
        serializer = UserRegistrationSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save(auth_provider='email')

            # Supprimer l'OTP du cache une fois le compte créé
            cache.delete(f"otp_{email}")

            tokens = get_user_tokens(user)
            return Response({
                'message': _('Inscription réussie.'),
                'tokens': tokens,
                'user': {'email': user.email, 'budget': user.monthly_budget}
            }, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get('email')
        password = request.data.get('password')

        # 1. Vérification préventive du fournisseur (Auth Provider)
        try:
            user_check = CustomUser.objects.get(email=email)
            if user_check.auth_provider == 'google':
                return Response(
                    {'error': _(
                        "Cette adresse e-mail est liée à Google. Veuillez utiliser le bouton 'Se connecter avec Google'.")},
                    status=status.HTTP_401_UNAUTHORIZED
                )
        except CustomUser.DoesNotExist:
            # L'utilisateur n'existe pas du tout, on laisse la suite gérer
            pass

        # 2. Authentification classique
        user = authenticate(email=email, password=password)

        if user is not None:
            tokens = get_user_tokens(user)
            return Response({
                'message': _('Connexion réussie.'),
                'tokens': tokens
            }, status=status.HTTP_200_OK)

        return Response({'error': _('E-mail ou mot de passe incorrect.')}, status=status.HTTP_401_UNAUTHORIZED)


class GoogleLoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        token = request.data.get('credential')
        user_data = verify_google_token(token)

        if not user_data:
            return Response({'error': _('Échec de l’authentification Google. Vérifiez votre compte et réessayez')},
                            status=status.HTTP_400_BAD_REQUEST)

        email = user_data.get('email')

        # 1. Vérification du compte existant
        try:
            user = CustomUser.objects.get(email=email)
            if user.auth_provider != CustomUser.AuthProviders.GOOGLE:
                # Le message précis qui sera affiché sur le front-end
                return Response(
                    {'error': _(
                        "Ce compte utilise un mot de passe. Veuillez utiliser le formulaire de connexion classique en haut.")},
                    status=status.HTTP_400_BAD_REQUEST
                )
        except CustomUser.DoesNotExist:
            # 2. Création du compte Google s'il n'existe pas
            user = CustomUser.objects.create_user(
                email=email,
                password=None,
                auth_provider=CustomUser.AuthProviders.GOOGLE,
            )

        tokens = get_user_tokens(user)
        return Response({
            'message': _('Authentification Google réussie.'),
            'tokens': tokens
        }, status=status.HTTP_200_OK)


class PasswordResetRequestAPIView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [PasswordResetAnonThrottle]

    def post(self, request):
        serializer = ResetPasswordEmailRequestSerializer(data=request.data)
        if serializer.is_valid():
            email = serializer.validated_data['email']
            try:
                user = CustomUser.objects.get(email=email)

                # Bloquer silencieusement si l'utilisateur s'est inscrit via Google
                if user.auth_provider == 'GOOGLE':
                    return Response(
                        {'error': _("Ce compte est lié à Google. Veuillez utiliser la connexion Google.")},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                # 1. Générer l'identifiant encodé et le token de sécurité
                uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
                token = PasswordResetTokenGenerator().make_token(user)

                # 2. Construire le lien qui pointera vers le front-end VUE.JS
                # (Ex: http://localhost:5173/reset-password/MTE/ajk123-token...)
                frontend_url = settings.FRONTEND_URL
                reset_url = f"{frontend_url}/reset-password/{uidb64}/{token}/"

                # 3. Envoyer l'e-mail via le service
                send_password_reset_email(email, reset_url)

            except CustomUser.DoesNotExist:
                # RÈGLE DE SÉCURITÉ : Ne jamais confirmer à un attaquant si un e-mail existe en base
                pass

            # On renvoie un message de succès générique dans tous les cas
            return Response(
                {'message': _(
                    "Si votre adresse e-mail existe dans notre base de données, vous recevrez un lien de réinitialisation sous peu.")},
                status=status.HTTP_200_OK
            )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PasswordResetConfirmAPIView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, uidb64, token):
        serializer = SetNewPasswordSerializer(data=request.data)

        if serializer.is_valid():
            try:
                # Decode the user ID
                uid = force_str(urlsafe_base64_decode(uidb64))
                user = CustomUser.objects.get(pk=uid)
            except (TypeError, ValueError, OverflowError, CustomUser.DoesNotExist):
                user = None

            # Verify the token is valid for this specific user
            if user is not None and PasswordResetTokenGenerator().check_token(user, token):
                # Set the new password securely
                user.set_password(serializer.validated_data['password'])
                user.save()
                return Response(
                    {'message': _('Votre mot de passe a été réinitialisé avec succès.')},
                    status=status.HTTP_200_OK
                )
            else:
                return Response(
                    {'error': _('Le lien de réinitialisation est invalide ou a expiré.')},
                    status=status.HTTP_400_BAD_REQUEST
                )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class UserProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        serializer = CustomUserSerializer(request.user)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request):
        user = request.user

        # 1. Gestion spécifique du changement de mot de passe
        new_password = request.data.get('new_password')

        # CORRECTION ICI : Remplacer 'current_password' par 'old_password'
        current_password = request.data.get('old_password')

        if new_password:
            # Sécurité : Bloquer les comptes Google
            if user.auth_provider == 'GOOGLE':
                return Response(
                    {"error": _("Impossible de modifier le mot de passe d'un compte géré par Google.")},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Vérification de l'ancien mot de passe
            if not current_password or not user.check_password(current_password):
                # Pour que le front puisse cibler le champ en erreur, on peut formater l'erreur
                return Response(
                    {"old_password": _("L'ancien mot de passe est incorrect.")},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Validation (longueur, complexité) et sauvegarde
            try:
                validate_password(new_password)
                user.set_password(new_password)
                user.save()  # On sauvegarde immédiatement le nouveau mot de passe
            except DjangoValidationError as e:
                # Renvoie la première erreur de validation de Django (ex: "trop court")
                return Response({"new_password": list(e.messages)[0]}, status=status.HTTP_400_BAD_REQUEST)

        # 2. Mise à jour du reste des données du profil (si présentes)
        serializer = CustomUserSerializer(user, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response({
                "message": _("Votre profil a été mis à jour avec succès."),
                "data": serializer.data
            }, status=status.HTTP_200_OK)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class RequestOTPView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get('email')
        if not email:
            return Response({"error": "L'adresse e-mail est requise."}, status=status.HTTP_400_BAD_REQUEST)

        # Vérifier si l'utilisateur existe déjà
        if CustomUser.objects.filter(email=email).exists():
            return Response({"error": "Un compte avec cet e-mail existe déjà."}, status=status.HTTP_400_BAD_REQUEST)

        # Générer un code à 6 chiffres
        otp_code = str(random.randint(100000, 999999))

        # Stocker dans le cache pour 10 minutes (600 secondes)
        cache.set(f"otp_{email}", otp_code, timeout=600)

        # Envoyer l'e-mail
        send_otp_email(email, otp_code)

        return Response({"message": "Code OTP envoyé avec succès."}, status=status.HTTP_200_OK)
class OnboardingChoicesView(APIView):
    permission_classes = [AllowAny]  # Or IsAuthenticated depending on your flow

    def get(self, request):
        return Response({
            "socio_pro": [{"value": c.value, "label": c.label} for c in SocioProChoices],
            "budget": [{"value": c.value, "label": c.label} for c in BudgetChoices]
        }, status=status.HTTP_200_OK)


class SubmitOnboardingView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        if user.is_onboarded:
            return Response({"error": "Utilisateur déjà onboardé."}, status=status.HTTP_400_BAD_REQUEST)

        serializer = OnboardingSerializer(user, data=request.data, partial=True)
        if serializer.is_valid():
            # Save the valid data and flag the user as onboarded
            serializer.save(is_onboarded=True)
            return Response({"message": "Onboarding terminé avec succès."}, status=status.HTTP_200_OK)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class ClearHistoryView(APIView):
    """
    Met à jour le timestamp history_cleared_at pour masquer
    les anciennes intentions d'achat du tableau de bord.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        request.user.history_cleared_at = timezone.now()
        request.user.save(update_fields=['history_cleared_at'])
        return Response({"message": _("Historique effacé avec succès.")}, status=status.HTTP_200_OK)


class ExtractProductInfoView(APIView):
    """
    Reçoit l'image depuis Vue.js, interroge l'IA et renvoie les données extraites
    pour la phase de 'Vérification' (sans sauvegarder l'intention d'achat).
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, *args, **kwargs):
        serializer = ProductImageExtractionSerializer(data=request.data)

        if serializer.is_valid():
            image_file = serializer.validated_data['image']
            try:
                extracted_data = extract_product_data_via_ai(image_file)
                return Response({
                    "message": _("Extraction réussie. Veuillez vérifier les informations."),
                    "data": extracted_data
                }, status=status.HTTP_200_OK)

            except Exception as e:
                # Journalisation de l'erreur d'API IA dans la base de données
                log_app_error(e, endpoint_url=request.path, user=request.user)
                return Response(
                    {"error": _(
                        "Impossible d'analyser l'image. L'IA est momentanément indisponible. Veuillez saisir les informations manuellement.")},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE
                )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PurchaseIntentionCreateView(APIView):
    """
    Crée l'intention d'achat finale une fois que l'utilisateur a validé
    (soit après extraction corrigée, soit après saisie 100% manuelle).
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request, *args, **kwargs):
        serializer = PurchaseIntentionSerializer(data=request.data)

        if serializer.is_valid():
            # Vérifie si l'utilisateur a forcé le passage
            is_bypassed = serializer.validated_data.get('is_incoherent_bypassed', False)

            # Si non forcé, on fait l'analyse de cohérence via l'IA
            if not is_bypassed:
                product_name = serializer.validated_data.get('product_name', '')
                product_category = serializer.validated_data.get('product_category', '')
                product_price = serializer.validated_data.get('product_price', 0)
                preferred_currency = getattr(request.user, 'preferred_currency', 'TND')

                prompt = f"""
                Vérifie la cohérence de cette intention d'achat :
                Nom : "{product_name}"
                Catégorie : "{product_category}"
                Prix : {product_price} {preferred_currency}

                Est-ce que ces trois éléments sont logiquement cohérents ensemble dans la réalité ? 
                Réponds STRICTEMENT par un JSON : {{"is_coherent": true/false, "reason": "explication brève"}}
                """
                try:

                    result = generate_gemini_json_response(prompt)

                    # On bloque si l'IA trouve ça incohérent
                    if not result.get('is_coherent', True):
                        from .models import AiWarningLog
                        AiWarningLog.objects.create(
                            user=request.user,
                            product_name=product_name,
                            product_category=product_category
                        )
                        return Response({
                            "error": result.get('reason',
                                                'Ces informations semblent incohérentes. Veuillez les vérifier.')
                        }, status=status.HTTP_400_BAD_REQUEST)
                except Exception as e:
                    # En cas d'erreur IA, on log et on laisse passer
                    log_app_error(e, context_message="Échec du garde-frontière IA", endpoint_url=request.path,
                                  user=request.user, level=ErrorLog.LogLevels.WARNING)

            # Sauvegarde finale
            purchase_intention = serializer.save(user=request.user)

            return Response({
                "message": _("Les informations ont été validées. L'IA prépare ses questions."),
                "id": purchase_intention.id
            }, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class GenerateQuestionsView(APIView):
    """
    Déclenche la génération des 3 questions par l'IA pour une intention donnée.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, intention_id):
        try:
            intention = get_user_intention_or_404(intention_id, request.user)
            if intention.questions.exists():
                questions = intention.questions.all()
            else:
                questions = generate_reflection_questions(intention.id)
            serializer = ReflectionQuestionSerializer(questions, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)
        except NotFound as e:
            # Let DRF handle the 404 naturally or catch it if you prefer custom formatting
            return Response({"error": str(e.detail)}, status=status.HTTP_404_NOT_FOUND)
        except Exception:
            return Response({"error": _("L'IA n'a pas pu générer les questions. Veuillez réessayer.")},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GenerateVerdictView(APIView):
    """
    Récupère les réponses aux questions, déclenche l'IA pour le verdict et met à jour l'intention.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, intention_id):
        try:
            with transaction.atomic():
                # 1. Vérification de sécurité : l'intention doit appartenir à l'utilisateur
                intention = get_user_intention_or_404(intention_id, request.user)

                # 2. Sauvegarde des réponses envoyées par Vue.js
                # On s'attend à recevoir : {"answers": [{"id": 1, "answer": "YES"}, ...]}
                answers_data = request.data.get('answers', [])
                questions_to_update = []
                for item in answers_data:
                    question = ReflectionQuestion.objects.filter(id=item.get('id'),
                                                                 purchase_intention=intention).first()
                    if question and item.get('answer'):
                        question.user_answer = str(item.get('answer'))
                        questions_to_update.append(question)
                if questions_to_update:
                    ReflectionQuestion.objects.bulk_update(questions_to_update, ['user_answer'])
                # 3. On vérifie que toutes les questions ont une réponse
                unanswered_count = intention.questions.filter(user_answer__isnull=True).count()
                if unanswered_count > 0:
                    return Response(
                        {"error": _("Veuillez répondre à toutes les questions avant d'obtenir le verdict.")},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                # 4. Génération du verdict via l'IA
                updated_intention = generate_ai_verdict(intention.id)

                # 5. Retour des données au front
                serializer = PurchaseIntentionSerializer(updated_intention)
            return Response(serializer.data, status=status.HTTP_200_OK)


        except NotFound as e:

            return Response({"error": str(e.detail)}, status=status.HTTP_404_NOT_FOUND)

        except Exception:

            return Response({"error": _("L'IA n'a pas pu rendre son verdict. Veuillez réessayer.")},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class UserFinalDecisionView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, intention_id):
        try:
            intention = get_user_intention_or_404(intention_id, request.user)
            serializer = FinalDecisionUpdateSerializer(intention, data=request.data, partial=True)

            if serializer.is_valid():
                updated_intention = serializer.save()

                update_fields = ['cooldown_expires_at'] # Liste des champs à MAJ

                # Gestion du chronomètre si la décision est "CALM" (Attendre)
                if updated_intention.user_final_decision == PurchaseIntention.DecisionChoices.CALM_DOWN:
                    hours = request.user.cooldown_preference or 24
                    updated_intention.cooldown_expires_at = timezone.now() + timedelta(hours=hours)
                    # NOUVEAU : On enregistre le fait qu'il a cliqué sur Attendre au moins une fois
                    if not updated_intention.wait_chosen_at:
                        updated_intention.wait_chosen_at = timezone.now()
                        update_fields.append('wait_chosen_at')
                else:
                    # On nettoie si l'utilisateur achète ou abandonne
                    updated_intention.cooldown_expires_at = None

                updated_intention.save(update_fields=update_fields)

                return Response({
                    "message": _("Votre décision finale a été enregistrée. Merci pour votre honnêteté !"),
                    "data": serializer.data
                }, status=status.HTTP_200_OK)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        except NotFound as e:
            return Response({"error": str(e.detail)}, status=status.HTTP_404_NOT_FOUND)


class DashboardSummaryView(APIView):
    """
    Vue unique et centralisée pour le Tableau de Bord.
    Renvoie les stats, le ratio, les objectifs, l'historique et les rappels.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        now = timezone.now()

        # Remise à UNKOWN des délais expirés
        PurchaseIntention.objects.filter(
            user=user,
            user_final_decision=PurchaseIntention.DecisionChoices.CALM_DOWN,
            cooldown_expires_at__lte=now
        ).update(
            user_final_decision=PurchaseIntention.DecisionChoices.UNKOWN,
            cooldown_expires_at=None
        )

        # BASE DE REQUÊTE SÉCURISÉE (Soft Delete Filter)
        # On filtre pour ne garder que les intentions créées APRÈS l'effacement de l'historique
        base_qs = PurchaseIntention.objects.filter(user=user)
        if user.history_cleared_at:
            base_qs = base_qs.filter(created_at__gte=user.history_cleared_at)

        # 1. Économies du mois
        monthly_savings = base_qs.filter(
            user_final_decision=PurchaseIntention.DecisionChoices.ABANDON,
            created_at__year=now.year,
            created_at__month=now.month
        ).aggregate(total=Sum('product_price'))['total'] or 0.00

        # 2. Ratio de Maîtrise (Basé uniquement sur les décisions finalisées)
        resolved_intentions = base_qs.filter(
            created_at__year=now.year,
            created_at__month=now.month
        ).exclude(
            Q(user_final_decision__isnull=True) | Q(user_final_decision=PurchaseIntention.DecisionChoices.UNKOWN)
        )

        total_resolved = resolved_intentions.count()
        abandoned_intentions = resolved_intentions.filter(
            user_final_decision=PurchaseIntention.DecisionChoices.ABANDON
        ).count()

        mastery_ratio = int((abandoned_intentions / total_resolved) * 100) if total_resolved > 0 else 0

        # 3. Objectif d'épargne actif
        active_goal = SavingsGoal.objects.filter(user=user, is_active=True).first()
        goal_data = {
            "goal_name": active_goal.goal_name,
            "target_amount": float(active_goal.target_amount),
            "saved_amount": float(active_goal.saved_amount)
        } if active_goal else None

        # 4. Historique récent (5 derniers éléments)
        # -> Les lignes de sérialisation manquantes étaient ici
        recent_intentions = base_qs.prefetch_related('questions').order_by('-created_at')[:5]
        history_serializer = PurchaseIntentionSerializer(recent_intentions, many=True)

        # 5. Analyses en attente (Bannière de rappel)
        pending_intentions = base_qs.filter(
            Q(user_final_decision__isnull=True) | Q(user_final_decision=PurchaseIntention.DecisionChoices.UNKOWN)
        ).order_by('-created_at')
        pending_serializer = PurchaseIntentionSerializer(pending_intentions, many=True)

        # Construction de la réponse unifiée
        return Response({
            "user_name": user.first_name or "Utilisateur",
            "ai_coach_message": "Vous avez fait de superbes économies ce mois-ci !" if float(
                monthly_savings) > 0 else "Prêt à sauver votre budget ?",
            "mastery_ratio": mastery_ratio,
            "savings_goal": goal_data,
            "stats": {
                "monthly_savings": float(monthly_savings),
                "current_month": now.strftime('%B'),
            },
            "recent_history": history_serializer.data,
            "pending_intentions": pending_serializer.data
        }, status=status.HTTP_200_OK)


class AppFeedbackCreateView(APIView):
    """
    Reçoit l'objet, le message (comment) et la note.
    Enregistre uniquement en base de données pour consultation par l'admin.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = AppFeedbackSerializer(data=request.data)

        if serializer.is_valid():
            # Enregistre le feedback et l'associe à l'utilisateur connecté
            serializer.save(user=request.user)

            return Response({
                "message": "Votre message a été envoyé avec succès !"
            }, status=status.HTTP_201_CREATED)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class StatsDashboardAPIView(APIView):
    def get(self, request):
        user = request.user
        period = request.query_params.get('period', 'month')
        category = request.query_params.get('category')

        # 1. Définition de la plage temporelle dynamique
        now = timezone.now()
        query_filter = Q(user=user)
        if user.history_cleared_at:
            query_filter &= Q(created_at__gte=user.history_cleared_at)
        if period == 'today':
            start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
            query_filter &= Q(created_at__gte=start_date)
        elif period == 'year':
            start_date = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            query_filter &= Q(created_at__gte=start_date)
        elif period == 'month':
            # Optionnel: 30 derniers jours ou depuis le 1er du mois. Ici, 30 derniers jours.
            start_date = now - timedelta(days=30)
            query_filter &= Q(created_at__gte=start_date)
        # Si period == 'all', on n'ajoute pas de filtre de date

        # 2. Construction du filtre par catégorie
        if category:
            query_filter &= Q(product_category=category)

        # 3. Agrégations directes via PostgreSQL
        stats = PurchaseIntention.objects.filter(query_filter).aggregate(
            total_saved=Sum('product_price', filter=Q(user_final_decision=PurchaseIntention.DecisionChoices.ABANDON)),
            count_abandoned=Count('id', filter=Q(user_final_decision=PurchaseIntention.DecisionChoices.ABANDON)),
            count_bought=Count('id', filter=Q(user_final_decision=PurchaseIntention.DecisionChoices.BUY)),
            count_waiting=Count('id', filter=Q(user_final_decision=PurchaseIntention.DecisionChoices.CALM_DOWN)),
        )

        # Nettoyage des valeurs Null
        total_saved = stats['total_saved'] or 0.00
        count_abandoned = stats['count_abandoned'] or 0
        count_bought = stats['count_bought'] or 0
        count_waiting = stats['count_waiting'] or 0

        # 4. Calcul du ratio de maîtrise (uniquement sur les décisions finales résolues)
        total_resolved = count_abandoned + count_bought
        mastery_rate = (count_abandoned / total_resolved * 100) if total_resolved > 0 else 0

        return Response({
            "summary": {
                "total_saved": float(total_saved),
                "abandoned_count": count_abandoned,
                "mastery_rate": round(mastery_rate, 1)
            },
            "chart_data": [
                {"label": "Abandonnés", "value": count_abandoned, "color": "#5A877E"},
                {"label": "Achetés", "value": count_bought, "color": "#EF4444"},
                {"label": "En réflexion", "value": count_waiting, "color": "#F59E0B"},
            ]
        })


class StandardResultsSetPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 50


class PurchaseHistoryView(generics.ListAPIView):
    """
    Endpoint intelligent pour la page de suivi.
    Sépare automatiquement les requêtes "En réflexion" des requêtes "Historique".
    """
    serializer_class = PurchaseIntentionSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        user = self.request.user
        queryset = PurchaseIntention.objects.filter(user=user)
        if user.history_cleared_at:
            queryset = queryset.filter(created_at__gte=user.history_cleared_at)

        # 1. FILTRE INTELLIGENT DU STATUT (Gère la séparation Haut/Bas)
        status_param = self.request.query_params.get('status')

        if status_param == 'Attendre':
            # Requête venant de la liste "En période de réflexion" (Haut)
            queryset = queryset.filter(user_final_decision=PurchaseIntention.DecisionChoices.CALM_DOWN)

        elif status_param in ['Acheté', 'Achet ']:
            # Requête venant des filtres de l'historique (Bas) - Tolérance pour les accents
            queryset = queryset.filter(user_final_decision=PurchaseIntention.DecisionChoices.BUY)

        elif status_param in ['Abandonné', 'Abandonn ']:
            # Requête venant des filtres de l'historique (Bas) - Tolérance pour les accents
            queryset = queryset.filter(user_final_decision=PurchaseIntention.DecisionChoices.ABANDON)

        else:
            # Requête par défaut (status='all') pour l'historique (Bas)
            # On exclut "Attendre" et "Inconnu" pour n'afficher que les actions clôturées
            queryset = queryset.filter(user_final_decision__in=[
                PurchaseIntention.DecisionChoices.BUY,
                PurchaseIntention.DecisionChoices.ABANDON
            ])

        # 2. FILTRE CATÉGORIE
        category = self.request.query_params.get('category')
        if category and category != 'all':
            queryset = queryset.filter(product_category=category)

        # 3. FILTRE RECHERCHE
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(product_name__icontains=search)

        # 4. FILTRE PRIX
        price = self.request.query_params.get('price')
        if price and price != 'all':
            if price == 'under500':
                queryset = queryset.filter(product_price__lt=500)
            elif price == '500to1500':
                queryset = queryset.filter(product_price__gte=500, product_price__lte=1500)
            elif price == 'over1500':
                queryset = queryset.filter(product_price__gt=1500)

        # 5. TRI
        sort_by = self.request.query_params.get('sort_by', 'date')
        if sort_by == 'price_asc':
            queryset = queryset.order_by('product_price')
        elif sort_by == 'price_desc':
            queryset = queryset.order_by('-product_price')
        elif sort_by == 'category':
            queryset = queryset.order_by('product_category')
        else:
            queryset = queryset.order_by('-created_at')

        return queryset


class AdminFeedbackListView(APIView):
    """
    Endpoint exclusif pour les administrateurs pour lire les tickets de support.
    """
    permission_classes = [IsAuthenticated, IsAdminUser]  # Bloque les utilisateurs normaux

    def get(self, request):
        # Fetch all feedbacks, newest first
        feedbacks = AppFeedback.objects.all().order_by('-created_at')
        serializer = AdminFeedbackSerializer(feedbacks, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class PurchaseIntentionDetailView(generics.RetrieveAPIView):
    """
    Permet de récupérer les détails d'une intention d'achat spécifique (pour la reprise de l'analyse).
    """
    serializer_class = PurchaseIntentionSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'id'
    lookup_url_kwarg = 'intention_id'

    def get_queryset(self):
        # Sécurité : l'utilisateur ne peut récupérer que ses propres intentions
        return PurchaseIntention.objects.filter(user=self.request.user)


def get_user_intention_or_404(intention_id, user):
    """Fetches a PurchaseIntention or raises a DRF NotFound exception."""
    try:
        return PurchaseIntention.objects.get(id=intention_id, user=user)
    except PurchaseIntention.DoesNotExist:
        raise NotFound(detail=_("Intention d'achat introuvable."))


class CategoryListView(APIView):
    permission_classes = [AllowAny]  # Categories are usually public knowledge

    def get(self, request):
        # Return a list of objects ready for Vue dropdowns
        categories = [
            {"value": choice.value, "label": choice.label}
            for choice in ProductCategoryChoices
        ]
        return Response(categories)


# --------------------------Partie spécifique pour l'Admin-------------------------

from django.db.models import Sum, Count, Q
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from .models import PurchaseIntention


class AdminGlobalStatsView(APIView):
    """Handles Global Impact Analytics and AI Effectiveness with Date Filtering."""
    permission_classes = [IsAuthenticated, IsAdminUser]

    def get(self, request):
        period = request.query_params.get('period', 'total')
        now = timezone.now()
        query_filter = Q()
        prev_query_filter = Q()

        # Application du filtre temporel (Actuel et Précédent pour la tendance)
        if period in ['today', 'day']:
            start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
            query_filter &= Q(created_at__gte=start_date)
            prev_query_filter &= Q(created_at__gte=start_date - timedelta(days=1), created_at__lt=start_date)
        elif period == 'week':
            start_date = now - timedelta(days=7)
            query_filter &= Q(created_at__gte=start_date)
            prev_query_filter &= Q(created_at__gte=start_date - timedelta(days=7), created_at__lt=start_date)
        elif period == 'month':
            start_date = now - timedelta(days=30)
            query_filter &= Q(created_at__gte=start_date)
            prev_query_filter &= Q(created_at__gte=start_date - timedelta(days=30), created_at__lt=start_date)
        elif period == 'year':
            start_date = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            query_filter &= Q(created_at__gte=start_date)
            prev_query_filter &= Q(created_at__gte=start_date.replace(year=start_date.year - 1),
                                   created_at__lt=start_date)

        # --- 1. Global Impact Aggregations ---
        total_saved_agg = PurchaseIntention.objects.filter(
            query_filter, user_final_decision=PurchaseIntention.DecisionChoices.ABANDON
        ).aggregate(total=Sum('product_price'))
        total_saved = total_saved_agg['total'] or 0.00

        resolved_qs = PurchaseIntention.objects.filter(query_filter).exclude(
            Q(user_final_decision__isnull=True) | Q(user_final_decision=PurchaseIntention.DecisionChoices.UNKOWN)
        )
        total_resolved = resolved_qs.count()
        total_abandoned = resolved_qs.filter(user_final_decision=PurchaseIntention.DecisionChoices.ABANDON).count()
        mastery_ratio = round((total_abandoned / total_resolved) * 100) if total_resolved > 0 else 0

        raw_category_distribution = list(
            PurchaseIntention.objects.filter(query_filter).values('product_category').annotate(count=Count('id'))
        )
        dist_dict = {item['product_category']: item['count'] for item in raw_category_distribution}
        category_distribution = [
            {"product_category": str(choice.label), "count": dist_dict.get(choice.value, 0)}
            for choice in ProductCategoryChoices
        ]

        from .models import AiWarningLog

        # --- 2. AI Effectiveness (Bypass KPI Enrichi) ---
        verdict_stats = []
        for verdict_code in [PurchaseIntention.DecisionChoices.BUY, PurchaseIntention.DecisionChoices.CALM_DOWN,
                             PurchaseIntention.DecisionChoices.ABANDON]:
            qs_verdict = PurchaseIntention.objects.filter(query_filter, ai_verdict=verdict_code)

            total_n = qs_verdict.count()
            final_bought = qs_verdict.filter(user_final_decision=PurchaseIntention.DecisionChoices.BUY).count()
            final_abandoned = qs_verdict.filter(user_final_decision=PurchaseIntention.DecisionChoices.ABANDON).count()
            wait_chosen = qs_verdict.filter(wait_chosen_at__isnull=False).count()
            wait_to_bought = qs_verdict.filter(wait_chosen_at__isnull=False,
                                               user_final_decision=PurchaseIntention.DecisionChoices.BUY).count()
            wait_to_abandoned = qs_verdict.filter(wait_chosen_at__isnull=False,
                                                  user_final_decision=PurchaseIntention.DecisionChoices.ABANDON).count()

            verdict_stats.append({
                "ai_verdict": verdict_code,
                "total_n": total_n,
                "final_bought_count": final_bought,
                "final_abandoned_count": final_abandoned,
                "wait_chosen_count": wait_chosen,
                "wait_to_bought_count": wait_to_bought,
                "wait_to_abandoned_count": wait_to_abandoned
            })

        # Calcul Période Actuelle
        total_warnings = AiWarningLog.objects.filter(query_filter).count()

        bypasses_qs = PurchaseIntention.objects.filter(query_filter, is_incoherent_bypassed=True)
        total_bypasses = bypasses_qs.count()

        # S'assure que les alertes ne soient jamais inférieures aux bypasses
        safe_warnings = max(total_warnings, total_bypasses)
        bypass_rate = round((total_bypasses / safe_warnings) * 100) if safe_warnings > 0 else 0

        # Calcul Période Précédente (Trend)
        prev_bypasses = PurchaseIntention.objects.filter(prev_query_filter, is_incoherent_bypassed=True).count()
        trend_diff = total_bypasses - prev_bypasses

        # Répartition pour le Bottom Sheet (Catégories les plus bypassées)
        bypassed_categories = list(
            bypasses_qs.values('product_category')
            .annotate(count=Count('id'))
            .order_by('-count')[:5]
        )

        return Response({
            "global_impact": {
                "total_saved": total_saved,
                "mastery_ratio": mastery_ratio,
                "category_distribution": category_distribution,
            },
            "ai_effectiveness": {
                "bypasses": total_bypasses,
                "warnings": safe_warnings,
                "bypass_rate": bypass_rate,
                "trend_diff": trend_diff,
                "details": {
                    "categories": bypassed_categories
                },
                "verdict_stats": verdict_stats
            }
        })


class AdminSystemHealthView(APIView):
    permission_classes = [IsAuthenticated, IsAdminUser]

    def get(self, request):
        logs = ErrorLog.objects.all()

        # 1. Extraction des paramètres de filtre
        status_filter = request.query_params.get('status')
        priority_filter = request.query_params.get('priority')
        search = request.query_params.get('search')
        assigned_to = request.query_params.get('assigned_to')
        period = request.query_params.get('period')

        # 2. Application des filtres
        if status_filter:
            logs = logs.filter(status=status_filter)
        if priority_filter:
            logs = logs.filter(priority=priority_filter)

        if search:
            logs = logs.filter(
                Q(error_message__icontains=search) |
                Q(endpoint_url__icontains=search) |
                Q(stack_trace__icontains=search)
            )

        if assigned_to == 'me':
            logs = logs.filter(assigned_to=request.user)
        elif assigned_to == 'unassigned':
            logs = logs.filter(assigned_to__isnull=True)

        if period:
            now = timezone.now()
            if period == '24h':
                logs = logs.filter(created_at__gte=now - timedelta(days=1))
            elif period == '7d':
                logs = logs.filter(created_at__gte=now - timedelta(days=7))

        # Tri et limite pour les performances
        logs = logs.order_by('-created_at')[:100]
        serializer = ErrorLogSerializer(logs, many=True)
        return Response(serializer.data)

    def patch(self, request, log_id):
        try:
            log = ErrorLog.objects.get(id=log_id)
            data = request.data.copy()

            # Action rapide : s'assigner le ticket sans connaître son propre ID frontend
            if data.get('action') == 'assign_me':
                log.assigned_to = request.user
                log.save()
                return Response(ErrorLogSerializer(log).data)

            serializer = ErrorLogSerializer(log, data=data, partial=True)
            if serializer.is_valid():
                serializer.save()
                return Response(serializer.data)
            return Response(serializer.errors, status=400)
        except ErrorLog.DoesNotExist:
            return Response({"error": "Log introuvable"}, status=404)


class AdminUserManagementView(APIView):
    """Handles User Profiles auditing, intervention, and search."""
    permission_classes = [IsAuthenticated, IsAdminUser]

    def get(self, request):
        search = request.query_params.get('search', '').strip()

        # 1. SÉCURITÉ : On exclut le(s) administrateur(s) de la liste
        users = CustomUser.objects.filter(is_staff=False, is_superuser=False).order_by('-date_joined')

        # Filtre de recherche basique sur email, prénom et nom
        if search:
            users = users.filter(
                Q(email__icontains=search) |
                Q(first_name__icontains=search) |
                Q(last_name__icontains=search)
            )

        serializer = AdminUserListSerializer(users, many=True)
        return Response(serializer.data)

    def patch(self, request, user_id):
        try:
            user = CustomUser.objects.get(id=user_id)
            action = request.data.get('action')

            # 2. SÉCURITÉ : On bloque toute tentative de modification sur un profil Admin
            if user.is_staff or user.is_superuser:
                return Response(
                    {"error": "Action non autorisée sur un compte administrateur."},
                    status=status.HTTP_403_FORBIDDEN
                )

            if action == 'soft_delete':
                user.is_active = False
                user.save()
                return Response({"status": "Utilisateur désactivé (Soft Delete)"})

            elif action == 'restore':
                user.is_active = True
                user.save()
                return Response({"status": "Utilisateur restauré avec succès"})

            elif action == 'manual_password_change':
                if user.auth_provider == 'GOOGLE':
                    return Response(
                        {"error": "Impossible de modifier le mot de passe d'un compte géré par Google."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                new_password = request.data.get('new_password')
                if not new_password or len(new_password) < 8:
                    return Response(
                        {"error": "Le mot de passe doit contenir au moins 8 caractères."},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                user.set_password(new_password)
                user.save()
                return Response({"status": "Mot de passe modifié avec succès."})

            elif action == 'reset_password':
                if user.auth_provider == 'GOOGLE':
                    return Response({"error": "Cannot reset password for Google Auth users"},
                                    status=status.HTTP_400_BAD_REQUEST)

                uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
                token = PasswordResetTokenGenerator().make_token(user)
                reset_url = f"{settings.FRONTEND_URL}/reset-password/{uidb64}/{token}/"
                send_password_reset_email(user.email, reset_url)
                return Response({"status": "Reset email sent"})

            return Response({"error": "Action non reconnue."}, status=status.HTTP_400_BAD_REQUEST)

        except CustomUser.DoesNotExist:
            return Response({"error": "Utilisateur introuvable."}, status=status.HTTP_404_NOT_FOUND)


class AdminCategoryStatsView(APIView):
    """Component-scoped API for the Category Distribution Widget."""
    permission_classes = [IsAuthenticated, IsAdminUser]

    def get(self, request):
        period = request.query_params.get('period', 'total')
        try:
            top_n = int(request.query_params.get('top_n', 7))
        except ValueError:
            top_n = 7

        now = timezone.now()
        query_filter = Q()

        if period in ['today', 'day']:
            start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
            query_filter &= Q(created_at__gte=start_date)
        elif period == 'week':
            start_date = now - timedelta(days=7)
            query_filter &= Q(created_at__gte=start_date)
        elif period == 'month':
            start_date = now - timedelta(days=30)
            query_filter &= Q(created_at__gte=start_date)
        elif period == 'year':
            start_date = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            query_filter &= Q(created_at__gte=start_date)

        # Get raw counts
        raw_distribution = list(
            PurchaseIntention.objects.filter(query_filter)
            .values('product_category')
            .annotate(count=Count('id'))
        )

        total_count = sum(item['count'] for item in raw_distribution)
        dist_dict = {item['product_category']: item['count'] for item in raw_distribution}

        all_categories = []
        for choice in ProductCategoryChoices:
            count = dist_dict.get(choice.value, 0)
            percent = round((count / total_count * 100) if total_count > 0 else 0, 1)
            all_categories.append({
                "key": choice.value,
                "label": str(choice.label),
                "value": count,
                "percent": percent
            })

        # Sort descending by count
        all_categories.sort(key=lambda x: x['value'], reverse=True)

        # Slice into Top N and Other
        top_categories = all_categories[:top_n]
        other_categories = all_categories[top_n:]

        # Filter out 0 values from top so the chart only draws existing data
        top_categories = [cat for cat in top_categories if cat['value'] > 0]

        other_value = sum(item['value'] for item in other_categories)
        other_percent = round((other_value / total_count * 100) if total_count > 0 else 0, 1)

        other_data = {
            "label": "Autres",
            "value": other_value,
            "percent": other_percent
        }

        return Response({
            "total": total_count,
            "top": top_categories,
            "other": other_data,
            "all": all_categories
        })
