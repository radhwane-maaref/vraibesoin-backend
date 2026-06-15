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
    """
    Sérialiseur pour la phase d'intégration (onboarding) de l'utilisateur.
    
    Gère la validation et l'enregistrement des catégories socio-professionnelles,
    des objectifs financiers, de la date de naissance et de la devise préférée.
    """

    class Meta:
        model = CustomUser
        fields = ['socio_professional_categories', 'financial_goals','birth_date', 'preferred_currency']

    def validate_socio_professional_categories(self, value):
        """
        Valide les catégories socio-professionnelles sélectionnées.

        Args:
            value (list): La liste des catégories sélectionnées.

        Returns:
            list: La liste validée.

        Raises:
            serializers.ValidationError: Si la liste est vide ou contient plus de 3 éléments.
        """
        if not value or not (1 <= len(value) <= 3):
            raise serializers.ValidationError("Veuillez sélectionner entre 1 et 3 catégories.")
        return value

    def validate_financial_goals(self, value):
        """
        Valide et normalise les objectifs financiers de l'utilisateur.

        Args:
            value (list): La liste des objectifs financiers bruts.

        Returns:
            list: La liste des objectifs normalisés sans doublons.

        Raises:
            serializers.ValidationError: Si le nombre d'objectifs uniques n'est pas compris entre 1 et 3.
        """
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
        """
        Vérifie que la date de naissance est logique (dans le passé).
        
        Args:
            value (datetime.date): La date de naissance fournie.

        Returns:
            datetime.date: La date validée.

        Raises:
            serializers.ValidationError: Si la date est dans le futur ou égale à aujourd'hui.
        """
        if value and value >= timezone.now().date():
            raise serializers.ValidationError(_("La date de naissance doit être dans le passé."))
        return value

    def validate_preferred_currency(self, value):
        """
        Standardise la devise en code ISO à 3 lettres majuscules.
        
        Args:
            value (str): Le code de la devise fourni.

        Returns:
            str: Le code de devise formaté et nettoyé.

        Raises:
            serializers.ValidationError: Si le code ne fait pas exactement 3 caractères.
        """
        if not value or len(value.strip()) != 3:
            raise serializers.ValidationError(_("Le format de la devise est invalide (ex: TND, EUR, USD)."))
        return value.upper().strip()


class CustomUserSerializer(serializers.ModelSerializer):
    """
    Sérialiseur complet pour le modèle CustomUser.
    
    Expose l'ensemble des informations de profil, incluant des champs
    en lecture seule calculés et des listes correctement formatées.
    """
    
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

    def validate_financial_goals(self, value):
        """
        Valide et formate les objectifs financiers lors des mises à jour de profil.

        Args:
            value (list or str): Les objectifs fournis (pouvant être en JSON string).

        Returns:
            list: La liste des objectifs validés.

        Raises:
            serializers.ValidationError: Si le format est incorrect ou le nombre d'éléments invalide.
        """
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
        """
        Valide et formate les catégories socio-professionnelles lors des mises à jour.

        Args:
            value (list or str): Les catégories fournies (pouvant être en JSON string).

        Returns:
            list: La liste des catégories validées.

        Raises:
            serializers.ValidationError: Si le format est incorrect, le nombre d'éléments
                invalide, ou en cas d'incompatibilité des choix.
        """
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
        """
        Récupère le nom complet de l'utilisateur.

        Args:
            obj (CustomUser): L'instance de l'utilisateur.

        Returns:
            str: La concaténation du prénom et du nom, sans espaces superflus.
        """
        return f"{obj.first_name} {obj.last_name}".strip()


class MonthlyChargeLedgerSerializer(serializers.ModelSerializer):
    """
    Sérialiseur pour le suivi mensuel des charges récurrentes.
    
    Expose les informations lues depuis le modèle de base (blueprint)
    pour un affichage consolidé.
    """
    
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
    """
    Sérialiseur pour la configuration des charges récurrentes.
    """
    
    due_date = serializers.DateField(write_only=True, required=False)

    class Meta:
        model = RecurringChargeBlueprint
        fields = ['id', 'name', 'is_fixed', 'exact_amount', 'min_amount', 'max_amount', 'due_date']

    def validate(self, attrs):
        """
        Valide la cohérence des montants selon si la charge est fixe ou variable.

        Args:
            attrs (dict): Le dictionnaire des attributs fournis.

        Returns:
            dict: Les attributs validés et nettoyés.

        Raises:
            serializers.ValidationError: Si la configuration des montants est incohérente.
        """
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
        """
        Crée une nouvelle instance et invalide le cache correspondant.

        Args:
            validated_data (dict): Les données validées.

        Returns:
            RecurringChargeBlueprint: L'instance créée.
        """
        instance = super().create(validated_data)
        from django.core.cache import cache
        cache.delete(f"user_charges_json_{instance.user.id}")
        return instance

    def update(self, instance, validated_data):
        """
        Met à jour une instance existante et invalide le cache correspondant.

        Args:
            instance (RecurringChargeBlueprint): L'instance à mettre à jour.
            validated_data (dict): Les données validées.

        Returns:
            RecurringChargeBlueprint: L'instance mise à jour.
        """
        instance = super().update(instance, validated_data)
        from django.core.cache import cache
        cache.delete(f"user_charges_json_{instance.user.id}")
        return instance


class UserRegistrationSerializer(serializers.ModelSerializer):
    """
    Sérialiseur pour l'inscription d'un nouvel utilisateur.
    
    Gère la validation du mot de passe et de sa confirmation, ainsi que
    le hachage sécurisé du mot de passe en base de données.
    """
    
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
        """
        Vérifie que les deux mots de passe fournis sont identiques.

        Args:
            attrs (dict): Le dictionnaire des attributs fournis.

        Returns:
            dict: Les attributs validés.

        Raises:
            serializers.ValidationError: Si les mots de passe ne correspondent pas.
        """
        password = attrs.get('password')
        confirm_password = attrs.get('confirm_password')

        if password != confirm_password:
            raise serializers.ValidationError({
                "confirm_password": _("Les mots de passe ne correspondent pas.")
            })

        return attrs

    def create(self, validated_data):
        """
        Crée un nouvel utilisateur en hachant le mot de passe.

        Args:
            validated_data (dict): Les données validées.

        Returns:
            CustomUser: L'utilisateur créé.
        """
        validated_data.pop('confirm_password', None)
        validated_data['password'] = make_password(validated_data.get('password'))
        return super().create(validated_data)


def validate_not_empty_string(value, error_message):
    """
    Assure qu'un champ texte n'est pas uniquement constitué d'espaces.

    Args:
        value (str): La valeur à valider.
        error_message (str): Le message d'erreur à renvoyer en cas d'échec.

    Returns:
        str: La valeur originale si elle est valide.

    Raises:
        serializers.ValidationError: Si la chaîne est vide ou ne contient que des espaces.
    """
    if not value.strip():
        raise serializers.ValidationError(error_message)
    return value


class ReflectionQuestionSerializer(serializers.ModelSerializer):
    """
    Sérialiseur pour les questions de réflexion générées par l'IA.
    """
    
    class Meta:
        model = ReflectionQuestion
        fields = ['id', 'purchase_intention', 'question_text', 'ai_options', 'user_answer']


class PurchaseIntentionSerializer(serializers.ModelSerializer):
    """
    Sérialiseur pour la gestion des intentions d'achat.
    
    Intègre les questions de réflexion imbriquées et effectue des validations
    contextuelles selon le type de portefeuille choisi (solde principal ou enveloppe).
    """
    
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

    def validate_product_price(self, value):
        """
        Valide que le prix est strictement positif.
        
        Args:
            value (Decimal): Le prix du produit.

        Returns:
            Decimal: Le prix validé.

        Raises:
            serializers.ValidationError: Si le prix est négatif ou nul.
        """
        if value <= 0:
            raise serializers.ValidationError(_("Le prix du produit doit être strictement positif."))
        return value

    def validate_product_name(self, value):
        """
        Vérifie que le nom du produit est valide et non vide.

        Args:
            value (str): Le nom du produit.

        Returns:
            str: Le nom validé.

        Raises:
            serializers.ValidationError: Si le nom est invalide.
        """
        return validate_not_empty_string(value, _("Le nom du produit est obligatoire."))

    def validate_product_category(self, value):
        """
        Vérifie que la catégorie du produit est valide et non vide.

        Args:
            value (str): La catégorie.

        Returns:
            str: La catégorie validée.

        Raises:
            serializers.ValidationError: Si la catégorie est invalide.
        """
        return validate_not_empty_string(value, _("La catégorie du produit est obligatoire."))

    def validate(self, attrs):
        """
        Effectue une validation globale de l'intention d'achat, notamment
        concernant la disponibilité des fonds dans le portefeuille sélectionné.

        Args:
            attrs (dict): Le dictionnaire des attributs fournis.

        Returns:
            dict: Les attributs validés.

        Raises:
            serializers.ValidationError: Si les fonds sont insuffisants ou l'enveloppe introuvable.
        """
        attrs = super().validate(attrs)

        wallet_type = attrs.get('wallet_type', 'main')
        product_price = attrs.get('product_price', 0)

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
    """
    Sérialiseur pour la consultation des journaux d'erreurs.
    """
    
    assigned_to_email = serializers.SerializerMethodField()

    class Meta:
        model = ErrorLog
        fields = '__all__'
        read_only_fields = [
            'id', 'created_at', 'error_message', 'endpoint_url', 'level',
            'http_method', 'stack_trace', 'user', 'resolved_at'
        ]

    def get_assigned_to_email(self, obj):
        """
        Récupère l'e-mail de la personne assignée à la résolution de l'erreur.

        Args:
            obj (ErrorLog): L'instance du journal d'erreur.

        Returns:
            str or None: L'e-mail de l'assigné, ou None.
        """
        return obj.assigned_to.email if obj.assigned_to else None


class ResetPasswordEmailRequestSerializer(serializers.Serializer):
    """
    Sérialiseur pour la requête de réinitialisation de mot de passe.
    """
    
    email = serializers.EmailField(
        min_length=2,
        error_messages={
            'invalid': _('Veuillez entrer une adresse e-mail valide.'),
            'blank': _('Ce champ est obligatoire.')
        }
    )


class SetNewPasswordSerializer(serializers.Serializer):
    """
    Sérialiseur pour la définition d'un nouveau mot de passe.
    
    Effectue les validations standards de sécurité Django sur le mot de passe.
    """
    
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
        """
        Vérifie la correspondance et la force du mot de passe.

        Args:
            attrs (dict): Le dictionnaire contenant le mot de passe et sa confirmation.

        Returns:
            dict: Les attributs validés.

        Raises:
            serializers.ValidationError: Si la confirmation échoue ou si le mot de passe est trop faible.
        """
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
    """
    Sérialiseur pour valider une image de produit téléchargée pour analyse.
    """
    
    image = serializers.ImageField(
        required=True,
        error_messages={
            'required': _("Une image est requise pour l'analyse."),
            'invalid': _("Le format de l'image est invalide.")
        }
    )

    def validate_image(self, value):
        """
        Contrôle la taille et le type MIME de l'image.

        Args:
            value (UploadedFile): Le fichier image.

        Returns:
            UploadedFile: Le fichier validé.

        Raises:
            serializers.ValidationError: Si le fichier dépasse 5 Mo ou n'a pas un format supporté.
        """
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
    """
    Sérialiseur pour la mise à jour de la décision finale d'une intention d'achat.
    """
    
    class Meta:
        model = PurchaseIntention
        fields = ['user_final_decision']
        extra_kwargs = {
            'user_final_decision': {'required': True}
        }

    def validate_user_final_decision(self, value):
        """
        Vérifie que la décision fournie fait partie des choix autorisés.

        Args:
            value (str): La décision finale de l'utilisateur.

        Returns:
            str: La décision validée.

        Raises:
            serializers.ValidationError: Si le choix n'est pas reconnu.
        """
        if value not in PurchaseIntention.DecisionChoices.values:
            raise serializers.ValidationError(_("Décision invalide."))
        return value

    def update(self, instance, validated_data):
        """
        Met à jour l'intention d'achat.
        La déduction financière est gérée exclusivement par UserFinalDecisionView
        qui utilise select_for_update() pour la sécurité transactionnelle.
        """
        return super().update(instance, validated_data)


class AppFeedbackSerializer(serializers.ModelSerializer):
    """
    Sérialiseur pour la soumission d'avis sur l'application.
    """
    
    class Meta:
        model = AppFeedback
        fields = ['id', 'user', 'rating', 'comment', 'created_at', 'subject']
        read_only_fields = ['id', 'user', 'created_at']

    def validate_rating(self, value):
        """
        Vérifie que la note se situe dans les bornes autorisées.

        Args:
            value (int): La note soumise.

        Returns:
            int: La note validée.

        Raises:
            serializers.ValidationError: Si la note n'est pas comprise entre 1 et 5.
        """
        if value < 1 or value > 5:
            raise serializers.ValidationError(_("La note doit être comprise entre 1 et 5 étoiles."))
        return value


class IncomeStreamSerializer(serializers.ModelSerializer):
    """
    Sérialiseur pour la gestion des sources de revenus.
    """
    
    class Meta:
        model = IncomeStream
        fields = ['id', 'name', 'amount', 'frequency', 'next_payment_date', 'is_active']

    def validate_amount(self, value):
        """
        Garantit que le montant du revenu est strictement positif.
        
        Args:
            value (Decimal): Le montant du revenu.

        Returns:
            Decimal: Le montant validé.

        Raises:
            serializers.ValidationError: Si le montant est inférieur ou égal à zéro.
        """
        if value <= 0:
            raise serializers.ValidationError(
                _("Le montant du revenu doit être strictement supérieur à zéro.")
            )
        return value


class BudgetEnvelopeSerializer(serializers.ModelSerializer):
    """
    Sérialiseur pour la gestion des enveloppes budgétaires.
    """
    
    class Meta:
        model = BudgetEnvelope
        fields = ['id', 'name', 'amount', 'total_spent', 'start_date', 'end_date', 'category', 'created_at']
        read_only_fields = ['id', 'created_at']

    def validate(self, attrs):
        """
        Effectue des contrôles de cohérence globaux sur l'enveloppe budgétaire.

        S'assure que les dates sont cohérentes et que les dépenses ne dépassent
        pas le budget alloué.

        Args:
            attrs (dict): Les attributs de l'enveloppe.

        Returns:
            dict: Les attributs validés.

        Raises:
            serializers.ValidationError: Si une incohérence temporelle ou financière est détectée.
        """
        start_date = attrs.get('start_date')
        end_date = attrs.get('end_date')
        amount = attrs.get('amount')
        total_spent = attrs.get('total_spent', 0)

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
    """
    Sérialiseur pour l'historique des transactions.
    
    Supporte la rétrocompatibilité pour le traitement de descriptions de type 'note' 
    et s'assure de l'association d'une catégorie pour les transactions essentielles.
    """
    
    note = serializers.CharField(write_only=True, required=False, allow_blank=True)
    category = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = TransactionHistory
        fields = ['id', 'amount', 'transaction_type', 'description', 'category', 'is_essential', 'note', 'date']
        read_only_fields = ['id', 'date', 'description']

    def validate(self, attrs):
        """
        Vérifie la cohérence de la transaction et génère automatiquement la description.

        Args:
            attrs (dict): Les attributs de la transaction.

        Returns:
            dict: Les attributs validés et formatés.

        Raises:
            serializers.ValidationError: Si la catégorie est manquante pour une dépense essentielle.
        """
        transaction_type = attrs.get('transaction_type')
        is_essential = attrs.get('is_essential', False)

        if is_essential and not attrs.get('category'):
            raise serializers.ValidationError({
                "category": _("La catégorie est requise pour une dépense essentielle.")
            })

        note = attrs.pop('note', '').strip()
        cat = attrs.get('category', '').strip()

        if note:
            attrs['description'] = f"{cat} - {note}" if cat else note
        else:
            attrs['description'] = cat if cat else _("Transaction manuelle")

        return attrs


class AdminFeedbackSerializer(serializers.ModelSerializer):
    """
    Sérialiseur d'interface administrateur pour l'affichage des avis.
    """
    
    user_email = serializers.EmailField(source='user.email', read_only=True)

    class Meta:
        model = AppFeedback
        fields = ['id', 'user_email', 'subject', 'rating', 'comment', 'created_at']


class AdminUserListSerializer(serializers.ModelSerializer):
    """
    Sérialiseur d'interface administrateur pour l'affichage synthétique des utilisateurs.
    """
    
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = CustomUser
        fields = [
            'id', 'email', 'first_name', 'last_name', 'full_name',
            'auth_provider', 'is_active', 'date_joined'
        ]

    def get_full_name(self, obj):
        """
        Récupère le nom complet de l'utilisateur.

        Args:
            obj (CustomUser): L'instance de l'utilisateur.

        Returns:
            str: Le nom complet.
        """
        return f"{obj.first_name} {obj.last_name}".strip()
