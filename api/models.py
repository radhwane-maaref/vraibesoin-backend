import uuid
import sys
from decimal import Decimal
from io import BytesIO
from PIL import Image

from django.conf import settings
from django.contrib.auth.base_user import BaseUserManager
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.core.validators import MinValueValidator, MaxValueValidator
from django.contrib.postgres.fields import ArrayField


class CustomUserManager(BaseUserManager):
    """
    Gestionnaire personnalisé pour le modèle utilisateur.

    Remplace le comportement par défaut de Django pour utiliser l'adresse
    e-mail comme identifiant de connexion principal au lieu du nom d'utilisateur.
    """

    def create_user(self, email, password=None, **extra_fields):
        """
        Crée et enregistre un utilisateur standard.

        Args:
            email (str): L'adresse e-mail de l'utilisateur.
            password (str, optional): Le mot de passe de l'utilisateur.
            **extra_fields: Champs supplémentaires pour l'utilisateur.

        Returns:
            CustomUser: L'instance de l'utilisateur créé.

        Raises:
            ValueError: Si l'adresse e-mail n'est pas fournie.
        """
        if not email:
            raise ValueError(_("L'adresse e-mail est obligatoire."))
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        """
        Crée et enregistre un super-utilisateur (administrateur).

        Args:
            email (str): L'adresse e-mail de l'utilisateur.
            password (str, optional): Le mot de passe de l'utilisateur.
            **extra_fields: Champs supplémentaires pour l'utilisateur.

        Returns:
            CustomUser: L'instance du super-utilisateur créé.

        Raises:
            ValueError: Si les attributs `is_staff` ou `is_superuser`
                ne sont pas définis sur True.
        """
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError(_('Superuser must have is_staff=True.'))
        if extra_fields.get('is_superuser') is not True:
            raise ValueError(_('Superuser must have is_superuser=True.'))

        return self.create_user(email, password, **extra_fields)


class BudgetChoices(models.TextChoices):
    """
    Énumération des tranches de budget.
    """
    UNDER_500 = 'Moins de 500', _('Moins de 500')
    BETWEEN_500_1000 = '500 - 1 000', _('500 - 1 000')
    BETWEEN_1000_2000 = '1 000 - 2 000', _('1 000 - 2 000')
    BETWEEN_2000_3500 = '2 000 - 3 500', _('2 000 - 3 500')
    BETWEEN_3500_5000 = '3 500 - 5 000', _('3 500 - 5 000')
    BETWEEN_5000_8000 = '5 000 - 8 000', _('5 000 - 8 000')
    BETWEEN_8000_15000 = '8 000 - 15 000', _('8 000 - 15 000')
    OVER_15000 = '15 000+', _('15 000+')
    PREFER_NOT_TO_SAY = 'Je préfère ne pas répondre', _('Je préfère ne pas répondre')


class SocioProChoices(models.TextChoices):
    """
    Énumération des catégories socio-professionnelles.
    """
    STUDENT = 'Étudiant', _('Étudiant')
    EMPLOYEE = 'Employé', _('Employé')
    CIVIL_SERVANT = 'Fonctionnaire', _('Fonctionnaire')
    MERCHANT = 'Commerçant / Artisan', _('Commerçant / Artisan')
    LIBERAL = 'Profession libérale', _('Profession libérale')
    FREELANCE = 'Indépendant / Freelance', _('Indépendant / Freelance')
    RETIRED = 'Retraité', _('Retraité')

    WORKER = 'Ouvrier / Technicien', _('Ouvrier / Technicien')
    UNEMPLOYED = 'Sans emploi', _('Sans emploi')
    HOMEMAKER = 'Au foyer', _('Au foyer')
    ENTREPRENEUR = 'Entrepreneur / Chef d\'entreprise', _('Entrepreneur / Chef d\'entreprise')
    OTHER = 'Autre', _('Autre')
    PREFER_NOT_TO_SAY = 'Préfère ne pas répondre', _('Je préfère ne pas répondre')


def default_socio_pro():
    """
    Fournit la catégorie socio-professionnelle par défaut.

    Returns:
        list: Liste contenant le choix par défaut "Préfère ne pas répondre".
    """
    return [SocioProChoices.PREFER_NOT_TO_SAY.value]


class CustomUser(AbstractUser):
    """
    Modèle d'utilisateur personnalisé.

    Remplace l'identifiant par défaut par l'adresse e-mail et ajoute des
    informations liées au profilage financier et psychologique.
    """

    username = None
    email = models.EmailField(_('email address'), unique=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []
    objects = CustomUserManager()

    class AuthProviders(models.TextChoices):
        """
        Énumération des fournisseurs d'authentification pris en charge.
        """
        EMAIL = 'EMAIL', _('Email')
        GOOGLE = 'GOOGLE', _('Google')

    class RigorChoices(models.TextChoices):
        """
        Énumération des niveaux de rigueur pour l'évaluation par l'IA.
        """
        INDULGENT = 'Indulgent', _('Indulgent')
        BALANCED = 'Équilibré', _('Équilibré')
        RUTHLESS = 'Impitoyable', _('Impitoyable')

    birth_date = models.DateField(null=True, blank=True)
    socio_professional_categories = models.JSONField(
        default=default_socio_pro,
        null=True,
        blank=True,
        help_text=_("Catégories socio-professionnelles (max 3)")
    )

    current_balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0.00,
        help_text=_("Solde actuel en temps réel")
    )
    financial_goals = ArrayField(
        models.CharField(max_length=200),
        size=3,
        blank=True,
        null=True,
        default=list
    )
    preferred_currency = models.CharField(max_length=3, default='TND')
    evaluation_rigor = models.CharField(
        max_length=20,
        choices=RigorChoices.choices,
        default=RigorChoices.BALANCED,
        help_text=_("Niveau de rigueur du coach IA")
    )
    cooldown_preference = models.IntegerField(
        default=24,
        help_text=_("Temps de réflexion par défaut en heures (12, 24, 48, 72)")
    )
    wants_cooldown_reminders = models.BooleanField(
        default=True,
        help_text=_("Recevoir une alerte avant la fin de la période de réflexion")
    )

    auth_provider = models.CharField(
        max_length=10,
        choices=AuthProviders.choices,
        default=AuthProviders.EMAIL,
        help_text=_("Méthode d'inscription utilisée par l'utilisateur")
    )
    google_id = models.CharField(max_length=255, null=True, blank=True, unique=True)
    is_onboarded = models.BooleanField(default=False)
    last_ip_address = models.GenericIPAddressField(null=True, blank=True)
    location_data = models.JSONField(default=dict, blank=True)
    history_cleared_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Date de la dernière réinitialisation de l'historique (Soft Delete)")
    )

    def delete(self, *args, **kwargs):
        """
        Désactive le compte utilisateur (Soft Delete) au lieu de le supprimer.

        Args:
            *args: Arguments positionnels optionnels.
            **kwargs: Arguments nommés optionnels.
            
        Returns:
            None
        """
        self.is_active = False
        self.save()

    def __str__(self):
        """
        Représentation sous forme de chaîne de caractères de l'utilisateur.

        Returns:
            str: L'adresse e-mail de l'utilisateur.
        """
        return self.email


class IncomeStream(models.Model):
    """
    Modèle représentant une source de revenus pour un utilisateur.
    """

    class FrequencyChoices(models.TextChoices):
        """
        Énumération des fréquences de versement des revenus.
        """
        ONE_TIME = 'ONE_TIME', _('Une seule fois')
        DAILY = 'DAILY', _('Quotidien')
        WEEKLY = 'WEEKLY', _('Hebdomadaire')
        MONTHLY = 'MONTHLY', _('Mensuel')
        YEARLY = 'YEARLY', _('Annuel')

    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='income_streams')
    name = models.CharField(max_length=150, help_text=_("Ex: Salaire, Freelance, Aide familiale"))
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    frequency = models.CharField(max_length=20, choices=FrequencyChoices.choices, default=FrequencyChoices.MONTHLY)
    next_payment_date = models.DateField(null=True, blank=True, help_text=_("Date du prochain versement"))
    is_active = models.BooleanField(default=True)
    is_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        """
        Représentation sous forme de chaîne de caractères de la source de revenu.

        Returns:
            str: Le nom, le montant et la fréquence du revenu.
        """
        return f"{self.name} - {self.amount} ({self.get_frequency_display()})"


class BudgetEnvelope(models.Model):
    """
    Modèle représentant une enveloppe budgétaire.

    Permet d'isoler des fonds pour des événements précis sans modifier
    le solde bancaire global de l'utilisateur.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='budget_envelopes'
    )
    name = models.CharField(max_length=150)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    total_spent = models.DecimalField(max_digits=10, decimal_places=2, default=0.00,
                                      help_text=_("Montant déjà dépensé dans cette enveloppe"))
    start_date = models.DateField()
    end_date = models.DateField()
    category = models.CharField(max_length=50, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        """
        Représentation sous forme de chaîne de caractères de l'enveloppe budgétaire.

        Returns:
            str: Nom, montant et adresse e-mail de l'utilisateur associé.
        """
        return f"{self.name} ({self.amount}) - {self.user.email}"


class TransactionHistory(models.Model):
    """
    Modèle pour enregistrer l'historique des transactions financières.
    """

    class TransactionType(models.TextChoices):
        """
        Énumération des types de transaction.
        """
        INCOME = 'INCOME', _('Revenu')
        EXPENSE = 'EXPENSE', _('Dépense')

    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='transactions')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    transaction_type = models.CharField(max_length=10, choices=TransactionType.choices)
    description = models.CharField(max_length=255)
    category = models.CharField(max_length=100, null=True, blank=True)
    is_essential = models.BooleanField(default=False)
    date = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date']

    def __str__(self):
        """
        Représentation sous forme de chaîne de caractères de la transaction.

        Returns:
            str: Le type de transaction, le montant et la description.
        """
        return f"{self.transaction_type}: {self.amount} - {self.description}"


class RecurringChargeBlueprint(models.Model):
    """
    Modèle de configuration pour une charge récurrente.

    Définit les paramètres de base (fixe ou variable, montant, jour d'exigibilité)
    pour la génération automatique des factures ou charges périodiques.
    """

    user = models.ForeignKey('CustomUser', on_delete=models.CASCADE, related_name='charge_blueprints')
    name = models.CharField(max_length=150, help_text=_("Ex: Électricité, Internet, Loyer"))
    is_fixed = models.BooleanField(default=True, help_text=_("Indique si le montant est fixe ou variable"))
    exact_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    min_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    max_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    due_day = models.IntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(31)],
        help_text=_("Le jour du mois où la charge est exigible")
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(is_fixed=True, exact_amount__isnull=False, min_amount__isnull=True, max_amount__isnull=True) |
                      Q(is_fixed=False, exact_amount__isnull=True, min_amount__isnull=False, max_amount__isnull=False),
                name='valid_amount_configuration'
            )
        ]

    def __str__(self):
        """
        Représentation sous forme de chaîne de caractères de la charge récurrente.

        Returns:
            str: Nom de la charge, jour d'exigibilité et e-mail de l'utilisateur.
        """
        return f"Blueprint: {self.name} (Jour {self.due_day}) - {self.user.email}"


class MonthlyChargeLedger(models.Model):
    """
    Modèle de suivi mensuel pour une charge récurrente.

    Représente l'instance spécifique d'une charge pour un cycle donné, 
    avec le montant provisionné et le montant réel payé.
    """

    blueprint = models.ForeignKey(RecurringChargeBlueprint, on_delete=models.CASCADE, related_name='ledger_records')
    name = models.CharField(max_length=150)
    provisioned_amount = models.DecimalField(max_digits=10, decimal_places=2,
                                             help_text=_("Le montant maximal verrouillé dans le coffre"))
    actual_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    due_date = models.DateField(help_text=_("Date précise d'échéance pour ce mois-ci"))
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['due_date']
        unique_together = ('blueprint', 'due_date')

    @property
    def is_paid(self):
        """
        Vérifie si la charge a été payée.

        Returns:
            bool: True si une date de paiement existe, False sinon.
        """
        return self.paid_at is not None

    def __str__(self):
        """
        Représentation sous forme de chaîne de caractères du suivi mensuel.

        Returns:
            str: Statut de paiement, nom de la charge et date d'échéance.
        """
        status = "Payé" if self.is_paid else "Sécurisé"
        return f"Ledger: {self.name} [{status}] pour le {self.due_date}"


class ProductCategoryChoices(models.TextChoices):
    """
    Énumération des catégories de produits pour les intentions d'achat.
    """
    SMARTPHONES = 'Smartphones', _('Smartphones')
    COMPUTERS = 'Ordinateurs & Tablettes', _('Ordinateurs & Tablettes')
    AUDIO = 'Audio & Écouteurs', _('Audio & Écouteurs')
    TECH_ACCESSORIES = 'Accessoires Tech (Coques, Câbles...)', _('Accessoires Tech (Coques, Câbles...)')

    GAMING_CONSOLES = 'Consoles de jeux', _('Consoles de jeux')
    VIDEO_GAMES = 'Jeux vidéo', _('Jeux vidéo')

    CLOTHING = 'Vêtements', _('Vêtements')
    SHOES = 'Chaussures & Sneakers', _('Chaussures & Sneakers')
    BAGS = 'Sacs & Maroquinerie', _('Sacs & Maroquinerie')
    JEWELRY = 'Bijoux & Montres', _('Bijoux & Montres')
    FASHION_ACCESSORIES = 'Accessoires de Mode', _('Accessoires de Mode')

    HOME_DECOR = 'Décoration d\'intérieur', _('Décoration d\'intérieur')
    FURNITURE = 'Meubles', _('Meubles')
    CANDLES_FRAGRANCES = 'Bougies & Parfums d\'ambiance', _('Bougies & Parfums d\'ambiance')
    KITCHENWARE = 'Ustensiles de cuisine & Vaisselle', _('Ustensiles de cuisine & Vaisselle')

    SMALL_APPLIANCES = 'Petit Électroménager', _('Petit Électroménager')
    LARGE_APPLIANCES = 'Gros Électroménager', _('Gros Électroménager')

    MAKEUP = 'Maquillage & Cosmétiques', _('Maquillage & Cosmétiques')
    SKINCARE = 'Soins de la peau', _('Soins de la peau')
    PERFUME = 'Parfums', _('Parfums')
    HAIRCARE = 'Soins des cheveux', _('Soins des cheveux')

    SPORTS_GEAR = 'Équipement de sport', _('Équipement de sport')
    COLLECTIBLES = 'Objets de collection & Figurines', _('Objets de collection & Figurines')
    BOOKS = 'Livres, Mangas & BD', _('Livres, Mangas & BD')
    ART_CRAFTS = 'Art & Loisirs créatifs', _('Art & Loisirs créatifs')

    RESTAURANTS_DELIVERY = 'Restaurants & Livraison', _('Restaurants & Livraison')
    COFFEE_SHOPS = 'Cafés & Salons de thé', _('Cafés & Salons de thé')
    SNACKS_ALCOHOL = 'Snacks & Alcool', _('Snacks & Alcool')

    DIGITAL_SUBSCRIPTIONS = 'Abonnements & Logiciels', _('Abonnements & Logiciels')
    IN_APP_PURCHASES = 'Achats in-app & Microtransactions', _('Achats in-app & Microtransactions')

    OTHER = 'Autre', _('Autre')


class PurchaseIntention(models.Model):
    """
    Modèle représentant une intention d'achat.

    Centralise les informations d'un achat potentiel, le verdict de l'IA,
    les délais de réflexion et la décision finale de l'utilisateur.
    """

    class DecisionChoices(models.TextChoices):
        """
        Énumération des décisions liées à une intention d'achat.
        """
        BUY = 'BUY', _('Acheter')
        CALM_DOWN = 'CALM', _('Calm Down (Réflexion)')
        ABANDON = 'ABANDON', _('Abandonner')
        UNKOWN = 'UNKOWN', _('Inconnu')

    class Meta:
        indexes = [
            models.Index(fields=['user', '-created_at']),
        ]

    wallet_type = models.CharField(
        max_length=50,
        default='main',
        help_text=_("Indique la source de financement ('main' pour solde principal, 'env_X' pour une enveloppe)")
    )
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True,
                             blank=True, related_name='purchase_intentions')
    product_name = models.CharField(max_length=100)
    product_price = models.DecimalField(max_digits=10, decimal_places=2)
    product_category = models.CharField(
        max_length=100,
        choices=ProductCategoryChoices.choices,
        default=ProductCategoryChoices.OTHER
    )
    usage_frequency = models.CharField(max_length=50, null=True, blank=True)
    has_similar_item = models.BooleanField(default=False)
    urgency_level = models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)], default=3)
    product_image = models.ImageField(upload_to='product_images/', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_incoherent_bypassed = models.BooleanField(
        default=False,
        help_text=_("Indique si l'utilisateur a forcé la création malgré l'alerte d'incohérence")
    )
    ai_verdict = models.CharField(
        max_length=10,
        choices=DecisionChoices.choices,
        null=True,
        blank=True,
        help_text=_("Le verdict rendu par l'IA")
    )
    ai_reasoning = models.TextField(null=True, blank=True,
                                    help_text=_("L'argumentaire généré par l'IA pour justifier son verdict"))
    user_final_decision = models.CharField(
        max_length=10,
        choices=DecisionChoices.choices,
        default=DecisionChoices.UNKOWN,
        null=True,
        blank=True,
        help_text=_("La décision finale prise par l'utilisateur")
    )
    wait_chosen_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Date à laquelle l'utilisateur a cliqué sur 'Attendre' pour la première fois")
    )
    cooldown_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Date d'expiration de la période de réflexion")
    )
    reminder_sent = models.BooleanField(
        default=False,
        help_text=_("Indique si l'e-mail de rappel de 2h a déjà été envoyé")
    )

    def save(self, *args, **kwargs):
        """
        Surcharge de la méthode save pour inclure la compression d'image.

        Vérifie si une nouvelle image a été uploadée et la compresse avant 
        la sauvegarde pour optimiser l'espace de stockage.

        Args:
            *args: Arguments positionnels optionnels.
            **kwargs: Arguments nommés optionnels.

        Returns:
            None
        """
        if self.product_image:
            is_new_image = False
            if self._state.adding:
                is_new_image = True
            else:
                orig = PurchaseIntention.objects.get(pk=self.pk)
                if orig.product_image != self.product_image:
                    is_new_image = True

            if is_new_image:
                try:
                    img = Image.open(self.product_image)

                    if img.mode in ("RGBA", "P"):
                        img = img.convert("RGB")

                    max_size = (1024, 1024)
                    img.thumbnail(max_size, Image.Resampling.LANCZOS)

                    output = BytesIO()
                    img.save(output, format='JPEG', quality=75, optimize=True)
                    output.seek(0)

                    name_without_ext = self.product_image.name.rsplit('.', 1)[
                        0] if '.' in self.product_image.name else self.product_image.name
                    self.product_image = InMemoryUploadedFile(
                        output,
                        'ImageField',
                        f"{name_without_ext}.jpg",
                        'image/jpeg',
                        sys.getsizeof(output),
                        None
                    )
                except Exception as e:
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.error(f"Error compressing image {self.product_name}: {e}")
                    from api.services import log_app_error
                    log_app_error(e, context_message=f"Error compressing image for intention {self.id}")

        super().save(*args, **kwargs)


class ReflectionQuestion(models.Model):
    """
    Modèle représentant une question de réflexion posée par l'IA.

    Associé à une intention d'achat spécifique, permet de stocker
    la question, les options proposées et la réponse de l'utilisateur.
    """

    purchase_intention = models.ForeignKey(PurchaseIntention, on_delete=models.CASCADE, related_name="questions")
    question_text = models.CharField(max_length=300)
    ai_options = models.JSONField(default=list, blank=True, null=True)
    user_answer = models.CharField(max_length=500, null=True, blank=True)

    def save(self, *args, **kwargs):
        """
        Surcharge de la méthode save pour valider la limite de questions.

        Vérifie qu'il n'y a pas plus de 3 questions associées à une
        intention d'achat lors de la création initiale.

        Args:
            *args: Arguments positionnels optionnels.
            **kwargs: Arguments nommés optionnels.

        Raises:
            ValidationError: Si le seuil maximal de 3 questions est atteint.
            
        Returns:
            None
        """
        if not self.pk:
            existing_questions_count = ReflectionQuestion.objects.filter(
                purchase_intention=self.purchase_intention
            ).count()

            if existing_questions_count >= 3:
                raise ValidationError(_("Une intention d'achat ne peut pas avoir plus de 3 questions de réflexion."))

        super().save(*args, **kwargs)

    def __str__(self):
        """
        Représentation sous forme de chaîne de caractères de la question de réflexion.

        Returns:
            str: Le début du texte de la question tronqué à 50 caractères.
        """
        return f"Q: {self.question_text[:50]}..."


class AppFeedback(models.Model):
    """
    Modèle pour enregistrer les retours et avis des utilisateurs.
    """

    user = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True)
    rating = models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)],
                                 help_text=_("Note de 1 à 5 étoiles"))
    subject = models.CharField(max_length=255, null=True, blank=True, help_text=_("Objet du message"))
    comment = models.TextField(null=True, blank=True, help_text=_("Commentaire facultatif"))
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        """
        Représentation sous forme de chaîne de caractères du retour utilisateur.

        Returns:
            str: L'utilisateur associé et l'objet du retour.
        """
        return f"Feedback de {self.user} - {self.subject or 'Sans objet'}"


class ErrorLog(models.Model):
    """
    Modèle d'enregistrement des erreurs système et applicatives.
    """

    class LogLevels(models.TextChoices):
        """
        Énumération des niveaux de sévérité d'une erreur.
        """
        WARNING = 'WARN', _('Avertissement')
        ERROR = 'ERROR', _('Erreur')
        CRITICAL = 'CRIT', _('Critique (Crash)')

    class LogStatus(models.TextChoices):
        """
        Énumération des statuts de traitement d'une erreur.
        """
        NEW = 'NEW', _('Nouveau')
        TRIAGED = 'TRIAGED', _('Trié')
        IN_PROGRESS = 'IN_PROGRESS', _('En cours')
        FIXED = 'FIXED', _('Corrigé')
        VERIFIED = 'VERIFIED', _('Vérifié')
        CLOSED = 'CLOSED', _('Fermé')

    class LogPriority(models.TextChoices):
        """
        Énumération des niveaux de priorité de résolution.
        """
        LOW = 'LOW', _('Basse')
        MEDIUM = 'MEDIUM', _('Moyenne')
        HIGH = 'HIGH', _('Haute')
        CRITICAL = 'CRITICAL', _('Critique')

    class HttpMethodChoices(models.TextChoices):
        """
        Énumération des méthodes HTTP impliquées dans l'erreur.
        """
        GET = 'GET', 'GET'
        POST = 'POST', 'POST'
        PUT = 'PUT', 'PUT'
        PATCH = 'PATCH', 'PATCH'
        DELETE = 'DELETE', 'DELETE'
        OPTIONS = 'OPTIONS', 'OPTIONS'
        HEAD = 'HEAD', 'HEAD'

    class Meta:
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['status', 'priority']),
        ]

    level = models.CharField(max_length=5, choices=LogLevels.choices, default=LogLevels.ERROR)
    error_message = models.TextField()
    endpoint_url = models.CharField(max_length=255, null=True, blank=True,
                                    help_text=_("L'URL de l'API où l'erreur s'est produite"))
    user = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True)
    is_resolved = models.BooleanField(default=False, help_text=_("Indique si le développeur a corrigé ce bug"))
    created_at = models.DateTimeField(auto_now_add=True)

    status = models.CharField(max_length=20, choices=LogStatus.choices, default=LogStatus.NEW)
    priority = models.CharField(max_length=20, choices=LogPriority.choices, default=LogPriority.MEDIUM)
    http_method = models.CharField(max_length=10, choices=HttpMethodChoices.choices, null=True, blank=True)
    stack_trace = models.TextField(null=True, blank=True)
    resolution_note = models.TextField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_error_logs'
    )

    def save(self, *args, **kwargs):
        """
        Surcharge de la méthode save pour la mise à jour du statut de résolution.

        Met à jour l'indicateur `is_resolved` et la date de résolution `resolved_at`
        selon le statut actuel de l'erreur.

        Args:
            *args: Arguments positionnels optionnels.
            **kwargs: Arguments nommés optionnels.

        Returns:
            None
        """
        resolved_statuses = [self.LogStatus.FIXED, self.LogStatus.VERIFIED, self.LogStatus.CLOSED]

        if self.status in resolved_statuses:
            self.is_resolved = True
            if not self.resolved_at:
                self.resolved_at = timezone.now()
        else:
            self.is_resolved = False

        super().save(*args, **kwargs)

    def __str__(self):
        """
        Représentation sous forme de chaîne de caractères du journal d'erreur.

        Returns:
            str: Identifiant, statut, priorité et début du message d'erreur.
        """
        short_msg = (self.error_message[:40] + '..') if len(self.error_message) > 40 else self.error_message
        return f"#{self.id} [{self.status}] {self.get_priority_display()} | {short_msg}"


class SavingsGoal(models.Model):
    """
    Modèle représentant un objectif d'épargne d'un utilisateur.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='savings_goals'
    )
    goal_name = models.CharField(max_length=150, help_text=_("Ex: Nouvel Ordinateur"))
    target_amount = models.DecimalField(max_digits=10, decimal_places=2)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        """
        Représentation sous forme de chaîne de caractères de l'objectif d'épargne.

        Returns:
            str: Nom de l'objectif et adresse e-mail de l'utilisateur.
        """
        return f"{self.goal_name} - {self.user.email}"


class AiWarningLog(models.Model):
    """
    Modèle pour enregistrer les alertes d'incohérence déclenchées par l'IA.

    Trace les avertissements générés avant la validation ou l'enregistrement
    d'une intention d'achat.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='warning_logs')
    product_name = models.CharField(max_length=255)
    product_category = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        """
        Représentation sous forme de chaîne de caractères du journal d'alerte IA.

        Returns:
            str: L'adresse e-mail de l'utilisateur et le nom du produit.
        """
        return f"Alerte IA: {self.user.email} - {self.product_name}"
