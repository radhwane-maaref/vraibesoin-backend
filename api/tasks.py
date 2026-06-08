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
    """Celery task to send emails asynchronously."""
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
    """Celery task to fetch and cache daily advice asynchronously."""
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
    """Celery task intended to run daily at midnight via Celery Beat."""
    today = timezone.now().date()

    # Get all active incomes due today or earlier (in case task missed a day)
    due_incomes = IncomeStream.objects.filter(is_active=True, next_payment_date__lte=today)

    for income in due_incomes:
        with transaction.atomic():
            # 1. Add amount to user's balance
            user = income.user
            user.current_balance += income.amount
            user.save(update_fields=['current_balance'])

            # 2. Log the transaction
            TransactionHistory.objects.create(
                user=user,
                amount=income.amount,
                transaction_type=TransactionHistory.TransactionType.INCOME,
                description=f"Revenu automatique : {income.name}"
            )

            # 3. Calculate next payment date
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
    Parcourt tous les Blueprints actifs pour instancier les lignes du nouveau mois
    et applique le verrouillage automatique des fonds.
    """
    today = timezone.now().date()
    # On cible le mois courant
    active_blueprints = RecurringChargeBlueprint.objects.filter(is_active=True)

    for blueprint in active_blueprints:
        # Construction de la date exacte pour ce mois-ci
        target_due_date = today.replace(day=min(blueprint.due_day, 28)) # Sécurité contre les fins de mois (ex: 29, 30, 31 février)

        # On vérifie s'il n'existe pas déjà pour éviter les doublons accidentels
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
                # Sécurisation automatique immédiate sur le solde de l'utilisateur
                user = blueprint.user
                user.current_balance -= blueprint.max_amount
                user.save(update_fields=['current_balance'])