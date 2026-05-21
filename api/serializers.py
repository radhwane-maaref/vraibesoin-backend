from rest_framework import serializers
from django.utils.translation import gettext_lazy as _
from .models import CustomUser, PurchaseIntention, ReflectionQuestion, AppFeedback, ErrorLog, SocioProChoices
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from django.contrib.auth.hashers import make_password
from django.utils.translation import gettext_lazy as _
import magic
import json


class OnboardingSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ['socio_professional_categories', 'monthly_budget', 'financial_goals']

    def validate_socio_professional_categories(self, value):
        if not value or not (1 <= len(value) <= 3):
            raise serializers.ValidationError("Veuillez sélectionner entre 1 et 3 catégories.")
        return value

    def validate_monthly_budget(self, value):
        if not value:
            raise serializers.ValidationError("La marge budgétaire est requise.")
        return value

    def validate_financial_goals(self, value):
        if not value or not (1 <= len(value) <= 3):
            raise serializers.ValidationError("Veuillez définir entre 1 et 3 objectifs financiers.")

        # Normalize and remove duplicates (case-insensitive)
        normalized = []
        seen = set()
        for goal in value:
            clean_goal = str(goal).strip()
            if clean_goal and clean_goal.lower() not in seen:
                seen.add(clean_goal.lower())
                normalized.append(clean_goal)

        if not (1 <= len(normalized) <= 3):
            raise serializers.ValidationError("Veuillez définir entre 1 et 3 objectifs uniques.")

        return normalized


class CustomUserSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()
    socio_professional_categories = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True
    )
    # FIX : Déclaration explicite du champ Array
    financial_goals = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True
    )

    class Meta:
        model = CustomUser

        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'full_name',
                  'birth_date', 'monthly_budget', 'profession', 'financial_goals',
                  'location_data', 'auth_provider', 'is_staff', 'cooldown_preference',
                  'evaluation_rigor', 'preferred_currency', 'wants_cooldown_reminders',
                  'socio_professional_categories', 'is_onboarded'
                  ]
        extra_kwargs = {
            'email': {'read_only': True},
            'username': {'read_only': True},
            'is_staff': {'read_only': True},
            'profession': {'read_only': True}
        }

    # FIX : Validation robuste et formatage de sauvegarde
    def validate_financial_goals(self, value):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except Exception:
                value = [v.strip() for v in value.split(',')]

        if not isinstance(value, list):
            raise serializers.ValidationError(_("Le format doit être une liste de chaînes de caractères."))

        value = [v for v in value if v]

        if len(value) < 1 or len(value) > 3:
            raise serializers.ValidationError(_("Veuillez définir entre 1 et 3 objectifs financiers."))

        normalized = []
        seen = set()
        for goal in value:
            clean_goal = str(goal).strip()
            if clean_goal and clean_goal.lower() not in seen:
                seen.add(clean_goal.lower())
                normalized.append(clean_goal)

        return normalized

    def validate_socio_professional_categories(self, value):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except Exception:
                value = [v.strip() for v in value.split(',')]

        if not isinstance(value, list):
            raise serializers.ValidationError(_("Le format doit être une liste de chaînes de caractères."))

        value = [v for v in value if v]

        if len(value) < 1 or len(value) > 3:
            raise serializers.ValidationError(_("Vous devez sélectionner entre 1 et 3 catégories."))

        if "Préfère ne pas répondre" in value and len(value) > 1:
            raise serializers.ValidationError(
                _("'Préfère ne pas répondre' ne peut pas être combiné avec d'autres choix."))

        return value

    def get_full_name(self, obj):
        return f"{obj.first_name} {obj.last_name}".strip()


class UserRegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True,
        required=True,
        style={'input_type': 'password'},
        error_messages={
            'blank': _('Le mot de passe est obligatoire.'),
            'required': _('Ce champ est requis.')
        }
    )
    confirm_password = serializers.CharField(
        write_only=True,
        required=True,
        style={'input_type': 'password'},
        error_messages={
            'blank': _('La confirmation du mot de passe est obligatoire.'),
            'required': _('Ce champ est requis.')
        }
    )

    class Meta:
        model = CustomUser
        fields = ['email', 'password', 'confirm_password']
        extra_kwargs = {
            'email': {
                'required': True,
                'error_messages': {
                    'blank': _("L'adresse e-mail est obligatoire."),
                    'invalid': _("Veuillez entrer une adresse e-mail valide.")
                }
            }
        }

    def validate(self, attrs):
        password = attrs.get('password')
        confirm_password = attrs.get('confirm_password')

        # API rejection if passwords do not match
        if password != confirm_password:
            raise serializers.ValidationError({
                "confirm_password": _("Les mots de passe ne correspondent pas.")
            })

        return attrs

    def create(self, validated_data):
        # Remove confirm_password because it doesn't exist on the CustomUser model
        validated_data.pop('confirm_password', None)

        # Hachage sécurisé du mot de passe avant l'enregistrement dans PostgreSQL
        validated_data['password'] = make_password(validated_data.get('password'))

        # Création de l'utilisateur avec les données validées et sécurisées
        return super().create(validated_data)


def validate_not_empty_string(value, error_message):
    """Utility to ensure string fields are not just whitespace."""
    if not value.strip():
        raise serializers.ValidationError(error_message)
    return value


class ReflectionQuestionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReflectionQuestion
        fields = ['id', 'purchase_intention', 'question_text', 'ai_options', 'user_answer']


class PurchaseIntentionSerializer(serializers.ModelSerializer):
    # Matches the related_name='questions' in the ReflectionQuestion model
    questions = ReflectionQuestionSerializer(many=True, read_only=True)

    class Meta:
        model = PurchaseIntention
        fields = [
            'id', 'user', 'product_name', 'product_price', 'product_category',
            'product_image', 'ai_verdict', 'ai_reasoning', 'user_final_decision',
            'created_at', 'updated_at', 'questions', 'usage_frequency', 'has_similar_item', 'urgency_level',
            'cooldown_expires_at', 'is_incoherent_bypassed'
        ]
        read_only_fields = [
            'id', 'user', 'ai_verdict', 'ai_reasoning',
            'user_final_decision', 'created_at', 'updated_at', 'cooldown_expires_at'
        ]

        def validate_product_price(self, value):
            """Valide que le prix est strictement positif."""
            if value <= 0:
                raise serializers.ValidationError(_("Le prix du produit doit être strictement positif."))
            return value

        def validate_product_name(self, value):
            """Évite qu'un utilisateur envoie un nom de produit composé uniquement d'espaces."""
            return validate_not_empty_string(value, _("Le nom du produit est obligatoire."))

        def validate_product_category(self, value):
            return validate_not_empty_string(value, _("La catégorie du produit est obligatoire."))


class ErrorLogSerializer(serializers.ModelSerializer):
    assigned_to_email = serializers.SerializerMethodField()

    class Meta:
        model = ErrorLog
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'error_message', 'endpoint_url', 'level', 'http_method', 'stack_trace',
                            'user', 'resolved_at']

    def get_assigned_to_email(self, obj):
        return obj.assigned_to.email if obj.assigned_to else None


class ResetPasswordEmailRequestSerializer(serializers.Serializer):
    email = serializers.EmailField(
        min_length=2,
        error_messages={
            'invalid': _('Veuillez entrer une adresse e-mail valide.'),
            'blank': _('Ce champ est obligatoire.')
        }
    )


class SetNewPasswordSerializer(serializers.Serializer):
    password = serializers.CharField(
        write_only=True,
        error_messages={
            'blank': _('Ce champ est obligatoire.')
        }
    )
    password_confirm = serializers.CharField(
        write_only=True,
        error_messages={
            'blank': _('Ce champ est obligatoire.')
        }
    )

    def validate(self, attrs):
        password = attrs.get('password')
        password_confirm = attrs.get('password_confirm')

        # 1. Check if passwords match
        if password != password_confirm:
            raise serializers.ValidationError({
                "password_confirm": _("Les mots de passe ne correspondent pas.")
            })

        # 2. Enforce Django's built-in password strength rules
        # (e.g., minimum length, not entirely numeric, etc.)
        try:
            validate_password(password)
        except DjangoValidationError as e:
            # Convert Django's validation errors into DRF validation errors
            raise serializers.ValidationError({
                "password": list(e.messages)
            })

        return attrs


class ProductImageExtractionSerializer(serializers.Serializer):
    image = serializers.ImageField(
        required=True,
        error_messages={
            'required': _("Une image est requise pour l'analyse."),
            'invalid': _("Le format de l'image est invalide.")
        }
    )

    def validate_image(self, value):
        # Validation stricte de la taille : 5 MB maximum
        max_size = 5 * 1024 * 1024
        if value.size > max_size:
            raise serializers.ValidationError(_("L'image ne doit pas dépasser 5 MB."))
        file_header = value.read(2048)
        value.seek(0)
        try:
            mime = magic.Magic(mime=True)
            actual_mime_type = mime.from_buffer(file_header)
        except Exception:
            raise serializers.ValidationError(_("Impossible de vérifier l'intégrité du fichier."))
        allowed_mime_types = [
            'image/jpeg',
            'image/png',
            'image/webp',

        ]
        if actual_mime_type not in allowed_mime_types:
            raise serializers.ValidationError(
                _("Fichier non autorisé. Seuls les formats JPEG, PNG et WEBP sont acceptés."))
        return value


class FinalDecisionUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = PurchaseIntention
        fields = ['user_final_decision']
        extra_kwargs = {
            'user_final_decision': {'required': True}
        }

    def validate_user_final_decision(self, value):
        # On s'assure que la décision fait partie des choix autorisés
        if value not in PurchaseIntention.DecisionChoices.values:
            raise serializers.ValidationError(_("Décision invalide."))
        return value


class AppFeedbackSerializer(serializers.ModelSerializer):
    class Meta:
        model = AppFeedback
        fields = ['id', 'user', 'rating', 'comment', 'created_at', 'subject']
        read_only_fields = ['id', 'user', 'created_at']

    def validate_rating(self, value):
        # Validation côté serveur de la note (1 à 5)
        if value < 1 or value > 5:
            raise serializers.ValidationError(_("La note doit être comprise entre 1 et 5 étoiles."))
        return value


# Partie administration
class AdminFeedbackSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source='user.email', read_only=True)

    class Meta:
        model = AppFeedback
        fields = ['id', 'user_email', 'subject', 'rating', 'comment', 'created_at']


class AdminUserListSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = CustomUser
        fields = [
            'id', 'email', 'first_name', 'last_name', 'full_name',
            'auth_provider', 'is_active', 'date_joined'
        ]

    def get_full_name(self, obj):
        return f"{obj.first_name} {obj.last_name}".strip()
