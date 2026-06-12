import traceback
import logging
from django.core.exceptions import (
    ValidationError as DjangoValidationError,
    ObjectDoesNotExist,
    PermissionDenied as DjangoPermissionDenied,
    SuspiciousOperation,
)
from django.http import Http404
from django.db import IntegrityError, OperationalError
from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework import status
from rest_framework.exceptions import (
    ValidationError as DRFValidationError,
    NotFound as DRFNotFound,
    PermissionDenied as DRFPermissionDenied,
    APIException,
)
from rest_framework.settings import api_settings
from api.models import ErrorLog

logger = logging.getLogger('api.exceptions')


class DatabaseConflictException(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Un conflit avec les données existantes est survenu."
    default_code = 'db_conflict'


class ServiceUnavailableException(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Le service est temporairement indisponible. Veuillez réessayer plus tard."
    default_code = 'service_unavailable'


def custom_exception_handler(exc, context):
    """
    Gestionnaire d'exceptions personnalisé global pour l'application.
    Intercepte les erreurs courantes (validation Django, existence, conflits DB, etc.)
    et les formate proprement pour l'API, tout en assurant un logging résilient.
    """

    # --- 1. Prétraitement et traduction des exceptions Django en exceptions DRF ---
    try:
        if isinstance(exc, DjangoValidationError):
            non_field_errors_key = api_settings.NON_FIELD_ERRORS_KEY
            if hasattr(exc, 'message_dict'):
                detail = dict(exc.message_dict)
                if '__all__' in detail:
                    detail[non_field_errors_key] = detail.pop('__all__')
            elif hasattr(exc, 'messages'):
                detail = exc.messages
            else:
                detail = str(exc)
            exc = DRFValidationError(detail=detail)

        elif isinstance(exc, ObjectDoesNotExist):
            exc = DRFNotFound(detail="La ressource demandée n'existe pas.")

        elif isinstance(exc, DjangoPermissionDenied):
            exc = DRFPermissionDenied(detail="Vous n'avez pas l'autorisation d'effectuer cette action.")

        elif isinstance(exc, Http404):
            exc = DRFNotFound(detail="La ressource demandée est introuvable.")

        elif isinstance(exc, IntegrityError):
            # Violation de contrainte unique, clé étrangère, etc.
            exc_str = str(exc).lower()
            if 'unique' in exc_str or 'duplicate' in exc_str or 'déjà existe' in exc_str or 'dÃ©jÃ  existe' in exc_str:
                detail = "Une ressource avec ces données existe déjà."
            elif 'check constraint' in exc_str or 'chk_' in exc_str or 'constraint' in exc_str:
                detail = "La requête enfreint une contrainte de validation de la base de données."
            elif 'foreign key' in exc_str or 'fk_' in exc_str:
                detail = "Une ressource liée requise est introuvable."
            else:
                detail = "Une contrainte d'intégrité de la base de données a été violée."
            exc = DatabaseConflictException(detail=detail)

        elif isinstance(exc, OperationalError):
            # Erreur de connexion/disponibilité de la base de données
            exc = ServiceUnavailableException(detail="Le service de base de données est temporairement indisponible.")

        elif isinstance(exc, SuspiciousOperation):
            # Requête suspecte (ex: en-tête Host invalide, etc.)
            exc = DRFValidationError(detail="Requête invalide ou suspecte.")

    except Exception as preprocessing_exc:
        # En cas d'erreur lors du prétraitement, on logue et on continue avec l'exception d'origine
        logger.error(f"Erreur lors du prétraitement de l'exception : {preprocessing_exc}")

    # --- 2. Exécution du gestionnaire par défaut de DRF ---
    response = exception_handler(exc, context)

    # Extraction des infos de contexte
    request = context.get('request')
    endpoint_url = None
    if request and hasattr(request, 'path'):
        endpoint_url = request.path[:255]

    # Extraction sécurisée de la méthode HTTP
    http_method = None
    if request and hasattr(request, 'method') and request.method in ErrorLog.HttpMethodChoices.values:
        http_method = request.method

    # Extraction sécurisée de l'utilisateur
    user = None
    try:
        if request and hasattr(request, 'user') and request.user and not request.user.is_anonymous:
            user = request.user
    except Exception:
        pass

    # Capture de la stack trace
    stack_trace = None
    if exc and hasattr(exc, '__traceback__') and exc.__traceback__:
        try:
            stack_trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        except Exception:
            pass
    if not stack_trace:
        try:
            stack_trace = traceback.format_exc()
        except Exception:
            stack_trace = "Stack trace non disponible"

    # --- 3. Détermination de la sévérité et du message de réponse ---
    if response is not None:
        # Erreur côté client (4xx) ou exception API gérée
        status_code = response.status_code
        error_message = f"[{status_code}] {str(response.data)[:1000]}"

        if status_code >= 500:
            level = ErrorLog.LogLevels.ERROR
            priority = ErrorLog.LogPriority.HIGH
        else:
            level = ErrorLog.LogLevels.WARNING
            priority = ErrorLog.LogPriority.LOW
    else:
        # Erreur interne non gérée (Crash 500)
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        error_message = f"{exc.__class__.__name__}: {str(exc)[:1000]}"
        level = ErrorLog.LogLevels.CRITICAL
        priority = ErrorLog.LogPriority.CRITICAL

        # Réponse générique pour éviter les fuites d'informations
        response = Response(
            {"detail": "Une erreur interne du serveur est survenue."},
            status=status_code
        )

    # --- 4. Enregistrement résilient dans la base de données ---
    try:
        ErrorLog.objects.create(
            level=level,
            error_message=error_message,
            endpoint_url=endpoint_url,
            user=user,
            status=ErrorLog.LogStatus.NEW,
            priority=priority,
            http_method=http_method,
            stack_trace=stack_trace
        )
    except Exception as db_exc:
        # Fallback sur le logging Python standard si la base de données est inaccessible (ex: OperationalError)
        logger.critical(
            f"Impossible d'enregistrer le log d'erreur en base de données. Erreur DB: {db_exc} | "
            f"Erreur d'origine: level={level}, message={error_message}, endpoint={endpoint_url}, method={http_method}"
        )

    return response