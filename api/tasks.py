from datetime import timedelta

from celery import shared_task
import logging

from dateutil.relativedelta import relativedelta
from django.core.mail import send_mail
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from api.models import TransactionHistory, MonthlyChargeLedger, RecurringChargeBlueprint, IncomeStream

logger = logging.getLogger(__name__)

@shared_task
def send_email_task(subject, message, recipient_list):
    """
    Tâche asynchrone pour l'envoi d'e-mails via Celery.

    Args:
        subject (str): L'objet de l'e-mail.
        message (str): Le contenu de l'e-mail.
        recipient_list (list): Liste des adresses e-mail destinataires.

    Returns:
        None
    """
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipient_list,
            fail_silently=False,
        )
        print(f"✅ SUCCESS: Email sent to {recipient_list}")
        logger.info(f"Email sent successfully to {recipient_list}")
    except Exception as e:
        import traceback
        print(f"❌ ERROR sending email to {recipient_list}: {str(e)}")
        traceback.print_exc()
        logger.error(f"Failed to send email to {recipient_list}: {e}")
        try:
            from api.services import log_app_error
            log_app_error(e, context_message=f"Erreur d'envoi d'e-mail à {recipient_list}")
        except Exception as inner_e:
            print(f"❌ ERROR saving to ErrorLog: {str(inner_e)}")


@shared_task
def fetch_and_cache_daily_advice_task(user_id):
    """
    Tâche asynchrone pour générer et mettre en cache le conseil quotidien d'un utilisateur.

    Args:
        user_id (int): L'identifiant de l'utilisateur concerné.

    Returns:
        None
    """
    from api.services import fetch_and_cache_daily_advice
    try:
        fetch_and_cache_daily_advice(user_id, execute_now=True)
        logger.info(f"Daily advice cached for user {user_id}")
    except Exception as e:
        logger.error(f"Failed to cache daily advice for user {user_id}: {e}")
        from api.services import log_app_error
        log_app_error(e, context_message=f"Erreur génération dynamic coach message pour l'utilisateur {user_id}")


@shared_task
def process_scheduled_incomes():
    """
    Exécute le traitement des revenus planifiés.

    Conçue pour être exécutée quotidiennement, cette tâche identifie les flux de revenus
    actifs arrivés à échéance, met à jour les soldes utilisateurs, historise les transactions,
    et planifie la prochaine date de paiement en fonction de la fréquence définie.

    Returns:
        None
    """
    today = timezone.now().date()

    due_incomes = IncomeStream.objects.filter(is_active=True, next_payment_date__lte=today)

    for income in due_incomes:
        with transaction.atomic():
            user = income.user
            user.current_balance += income.amount
            user.save(update_fields=['current_balance'])

            TransactionHistory.objects.create(
                user=user,
                amount=income.amount,
                transaction_type=TransactionHistory.TransactionType.INCOME,
                description=f"Revenu automatique : {income.name}"
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

    logger.info(f"Processed {due_incomes.count()} scheduled incomes.")


@shared_task
def generate_monthly_ledger_for_new_cycle():
    """
    Parcourt l'ensemble des plans de charges récurrentes (Blueprints) actifs pour instancier 
    les enregistrements mensuels (Ledgers) correspondants.
    
    Applique automatiquement le verrouillage des fonds sur le solde de l'utilisateur
    en déduisant le montant maximum prévu pour chaque charge.

    Returns:
        None
    """
    today = timezone.now().date()
    active_blueprints = RecurringChargeBlueprint.objects.filter(is_active=True)

    for blueprint in active_blueprints:
        # Sécurité contre les débordements de fin de mois (ex: 29, 30, 31 février n'existant pas)
        target_due_date = today.replace(day=min(blueprint.due_day, 28))

        exists = MonthlyChargeLedger.objects.filter(blueprint=blueprint, due_date=target_due_date).exists()
        if not exists:
            with transaction.atomic():
                MonthlyChargeLedger.objects.create(
                    blueprint=blueprint,
                    user=blueprint.user,
                    name=blueprint.name,
                    is_fixed=blueprint.is_fixed,
                    max_amount=blueprint.max_amount,
                    min_amount=blueprint.min_amount,
                    exact_amount=blueprint.exact_amount,
                    due_date=target_due_date
                )
                
                user = blueprint.user
                user.current_balance -= blueprint.max_amount
                user.save(update_fields=['current_balance'])