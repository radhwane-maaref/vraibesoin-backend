from celery import shared_task
import logging
from django.core.mail import send_mail
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

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
        fetch_and_cache_daily_advice(user_id)
        logger.info(f"Daily advice cached for user {user_id}")
    except Exception as e:
        logger.error(f"Failed to cache daily advice for user {user_id}: {e}")
        from api.services import log_app_error
        log_app_error(e, context_message=f"Erreur génération dynamic coach message pour l'utilisateur {user_id}")