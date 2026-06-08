from django.db.models.aggregates import Sum
from django.utils import timezone
from rest_framework import serializers
from django.utils.translation import gettext_lazy as _
from .models import (
    CustomUser, PurchaseIntention, ReflectionQuestion, AppFeedback, ErrorLog,
    SocioProChoices, IncomeStream, TransactionHistory, MonthlyChargeLedger,
    RecurringChargeBlueprint, BudgetEnvelope
)
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.contrib.auth.hashers import make_password
import magic
import json


class OnboardingSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ['socio_professional_categories', 'financial_goals','birth_date', 'preferred_currency']

    def validate_socio_professional_categories(self, value):
        if not value or not (1 <= len(value) <= 3):
            raise serializers.ValidationError("Veuillez sélectionner entre 1 et 3 catégories.")
        return value

    def validate_financial_goals(self, value):
        # [GARDER VOTRE LOGIQUE ACTUELLE DE VALIDATION ICI]
        # (La normalisation et la suppression des doublons)
        normalized = []
        seen = set()
        for goal in value:
            clean_goal = str(goal).strip()
            if clean_goal and clean_goal.lower() not in seen:
                seen.add(clean_goal.lower())
                normalized.append(clean_goal)

        if not (1 <= len(normalized) <= 3):
            raise serializers.ValidationError(_("Veuillez définir entre 1 et 3 objectifs uniques."))

        return normalized

    def validate_birth_date(self, value):
        """Vérifie que la date de naissance est logique (dans le passé)."""
        if value and value >= timezone.now().date():
            raise serializers.ValidationError(_("La date de naissance doit être dans le passé."))
        return value

    def validate_preferred_currency(self, value):
        """Standardise la devise en code ISO à 3 lettres majuscules."""
        if not value or len(value.strip()) != 3:
            raise serializers.ValidationError(_("Le format de la devise est invalide (ex: TND, EUR, USD)."))
        return value.upper().strip()

class CustomUserSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()
    socio_professional_categories = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True
    )
    financial_goals = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True
    )

    class Meta:
        model = CustomUser
        fields = [
            'id', 'username', 'email', 'first_name', 'last_name', 'full_name',
            'birth_date', 'financial_goals',
            'location_data', 'auth_provider', 'is_staff', 'cooldown_preference',
            'evaluation_rigor', 'preferred_currency', 'wants_cooldown_reminders',
            'socio_professional_categories', 'is_onboarded', 'current_balance'
        ]
        extra_kwargs = {
            'email': {'read_only': True},
            'username': {'read_only': True},
            'is_staff': {'read_only': True},
            'current_balance': {'read_only': True}
        }

    # ✅ Rapatrié et aligné correctement dans CustomUserSerializer
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

    # ✅ Rapatrié et aligné correctement dans CustomUserSerializer
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

    # ✅ Sorti de l'imbrication et rattaché à CustomUserSerializer
    def get_full_name(self, obj):
        return f"{obj.first_name} {obj.last_name}".strip()


class MonthlyChargeLedgerSerializer(serializers.ModelSerializer):
    is_fixed = serializers.ReadOnlyField(source='blueprint.is_fixed')
    max_amount = serializers.ReadOnlyField(source='blueprint.max_amount')
    min_amount = serializers.ReadOnlyField(source='blueprint.min_amount')
    exact_amount = serializers.ReadOnlyField(source='blueprint.exact_amount')
    actual_amount_paid = serializers.ReadOnlyField(source='actual_amount')

    class Meta:
        model = MonthlyChargeLedger
        fields = [
            'id', 'name', 'is_fixed', 'max_amount', 'min_amount',
            'exact_amount', 'due_date', 'is_paid', 'actual_amount_paid'
        ]


class RecurringChargeBlueprintSerializer(serializers.ModelSerializer):
    due_date = serializers.DateField(write_only=True, required=False)

    class Meta:
        model = RecurringChargeBlueprint
        fields = ['id', 'name', 'is_fixed', 'exact_amount', 'min_amount', 'max_amount', 'due_date']

    def validate(self, attrs):
        is_fixed = attrs.get('is_fixed', False)
        if is_fixed:
            attrs['min_amount'] = None
            attrs['max_amount'] = None
            if attrs.get('exact_amount') is None:
                raise serializers.ValidationError(
                    {"exact_amount": "Le montant exact est obligatoire pour ce champ."}
                )
        else:
            attrs['exact_amount'] = None
            min_amt = attrs.get('min_amount')
            max_amt = attrs.get('max_amount')
            if min_amt is None or min_amt < 0:
                raise serializers.ValidationError(_("Le montant minimal doit être supérieur ou égal à 0."))
            if max_amt is None or max_amt <= min_amt:
                raise serializers.ValidationError(
                    _("Le montant maximal doit être strictement supérieur au montant minimal."))
        return attrs

    def create(self, validated_data):
        instance = super().create(validated_data)
        # Invalidation du cache Upstash Redis pour ce user
        from django.core.cache import cache
        cache.delete(f"user_charges_json_{instance.user.id}")
        return instance

    def update(self, instance, validated_data):
        instance = super().update(instance, validated_data)
        # Invalidation du cache Upstash Redis pour ce user
        from django.core.cache import cache
        cache.delete(f"user_charges_json_{instance.user.id}")
        return instance


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

        if password != confirm_password:
            raise serializers.ValidationError({
                "confirm_password": _("Les mots de passe ne correspondent pas.")
            })

        return attrs

    def create(self, validated_data):
        validated_data.pop('confirm_password', None)
        validated_data['password'] = make_password(validated_data.get('password'))
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
    questions = ReflectionQuestionSerializer(many=True, read_only=True)

    class Meta:
        model = PurchaseIntention
        fields = [
            'id', 'user', 'product_name', 'product_price', 'product_category',
            'product_image', 'ai_verdict', 'ai_reasoning', 'user_final_decision',
            'created_at', 'updated_at', 'questions', 'usage_frequency', 'has_similar_item', 'urgency_level',
            'cooldown_expires_at', 'is_incoherent_bypassed', 'wallet_type'
        ]
        read_only_fields = [
            'id', 'user', 'ai_verdict', 'ai_reasoning',
            'user_final_decision', 'created_at', 'updated_at', 'cooldown_expires_at'
        ]

    # ✅ Désindenté d'un cran : Désormais rattaché à la classe principale et actif
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

    def validate(self, attrs):
        # Valider d'abord le reste via parent
        attrs = super().validate(attrs)

        wallet_type = attrs.get('wallet_type', 'main')
        product_price = attrs.get('product_price', 0)

        # On ne vérifie le solde que lors de la création initiale (POST)
        request = self.context.get('request')
        if request and request.method == 'POST':
            user = request.user

            if wallet_type == 'main':
                today = timezone.now().date()
                active_envelopes = BudgetEnvelope.objects.filter(
                    user=user,
                    start_date__lte=today,
                    end_date__gte=today
                )
                reserved_amount = active_envelopes.aggregate(Sum('amount'))['amount__sum'] or 0
                available_balance = user.current_balance - reserved_amount

                if product_price > available_balance:
                    raise serializers.ValidationError(
                        {"wallet_type": _("Le solde du portefeuille principal est insuffisant.")})

            elif wallet_type.startswith('env_'):
                try:
                    env_id = wallet_type.split('_')[1]
                    envelope = BudgetEnvelope.objects.get(id=env_id, user=user)
                    if product_price > envelope.amount:
                        raise serializers.ValidationError(
                            {"wallet_type": _("Le solde de cette enveloppe est insuffisant.")})
                except (IndexError, BudgetEnvelope.DoesNotExist):
                    raise serializers.ValidationError(
                        {"wallet_type": _("Enveloppe budgétaire invalide ou introuvable.")})

        return attrs


class ErrorLogSerializer(serializers.ModelSerializer):
    assigned_to_email = serializers.SerializerMethodField()

    class Meta:
        model = ErrorLog
        fields = '__all__'
        read_only_fields = [
            'id', 'created_at', 'error_message', 'endpoint_url', 'level',
            'http_method', 'stack_trace', 'user', 'resolved_at'
        ]

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

        if password != password_confirm:
            raise serializers.ValidationError({
                "password_confirm": _("Les mots de passe ne correspondent pas.")
            })

        try:
            validate_password(password)
        except DjangoValidationError as e:
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

        allowed_mime_types = ['image/jpeg', 'image/png', 'image/webp']
        if actual_mime_type not in allowed_mime_types:
            raise serializers.ValidationError(
                _("Fichier non autorisé. Seuls les formats JPEG, PNG et WEBP sont acceptés.")
            )
        return value


class FinalDecisionUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = PurchaseIntention
        fields = ['user_final_decision']
        extra_kwargs = {
            'user_final_decision': {'required': True}
        }

    def validate_user_final_decision(self, value):
        if value not in PurchaseIntention.DecisionChoices.values:
            raise serializers.ValidationError(_("Décision invalide."))
        return value


class AppFeedbackSerializer(serializers.ModelSerializer):
    class Meta:
        model = AppFeedback
        fields = ['id', 'user', 'rating', 'comment', 'created_at', 'subject']
        read_only_fields = ['id', 'user', 'created_at']

    def validate_rating(self, value):
        if value < 1 or value > 5:
            raise serializers.ValidationError(_("La note doit être comprise entre 1 et 5 étoiles."))
        return value


class IncomeStreamSerializer(serializers.ModelSerializer):
    class Meta:
        model = IncomeStream
        fields = ['id', 'name', 'amount', 'frequency', 'next_payment_date', 'is_active']

    def validate_amount(self, value):
        """
        Garantit que le montant du revenu est strictement supérieur à zéro.
        """
        if value <= 0:
            raise serializers.ValidationError(
                _("Le montant du revenu doit être strictement supérieur à zéro.")
            )
        return value

class BudgetEnvelopeSerializer(serializers.ModelSerializer):
    class Meta:
        model = BudgetEnvelope
        fields = ['id', 'name', 'amount', 'total_spent', 'start_date', 'end_date', 'category', 'created_at']
        read_only_fields = ['id', 'created_at']

    def validate(self, attrs):
        start_date = attrs.get('start_date')
        end_date = attrs.get('end_date')
        amount = attrs.get('amount')
        total_spent = attrs.get('total_spent', 0)

        # Validation : La date de fin doit être >= date de début
        if start_date and end_date and end_date < start_date:
            raise serializers.ValidationError({
                "dates": _("La date de fin doit être postérieure ou égale à la date de début.")
            })
        if total_spent < 0:
            raise serializers.ValidationError({
                "total_spent": _("Les dépenses ne peuvent pas être négatives.")
            })

        if amount is not None and total_spent > amount:
            raise serializers.ValidationError({
                "total_spent": _("Les dépenses ne peuvent pas dépasser le montant alloué de l'enveloppe.")
            })

        return attrs


class TransactionHistorySerializer(serializers.ModelSerializer):
    note = serializers.CharField(write_only=True, required=False, allow_blank=True)
    category = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = TransactionHistory
        fields = ['id', 'amount', 'transaction_type', 'description', 'category', 'is_essential', 'note', 'date']
        read_only_fields = ['id', 'date', 'description']

    def validate(self, attrs):
        transaction_type = attrs.get('transaction_type')
        is_essential = attrs.get('is_essential', False)

        # 1. Validation de la catégorie si c'est une dépense essentielle
        if is_essential and not attrs.get('category'):
            raise serializers.ValidationError({
                "category": _("La catégorie est requise pour une dépense essentielle.")
            })

        # 2. Rétrocompatibilité : Mapper 'note' vers 'description'
        note = attrs.pop('note', '').strip()
        cat = attrs.get('category', '').strip()

        if note:
            attrs['description'] = f"{cat} - {note}" if cat else note
        else:
            attrs['description'] = cat if cat else _("Transaction manuelle")

        return attrs


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
