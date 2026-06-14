"""
Gestionnaire d'exceptions et exceptions personnalisées pour l'API.

Ce module définit les exceptions métier spécifiques et surcharge le gestionnaire
d'exceptions par défaut de Django REST Framework pour assurer une journalisation
systématique des erreurs en base de données, ainsi que la standardisation des
réponses HTTP renvoyées au client.
"""

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
    """
    Exception levée lors d'un conflit d'intégrité au niveau de la base de données.
    
    Retourne une réponse HTTP 409 (Conflict).
    """
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Un conflit avec les données existantes est survenu."
    default_code = 'db_conflict'


class ServiceUnavailableException(APIException):
    """
    Exception levée lorsque le service est temporairement incapable de traiter la requête.
    
    Retourne une réponse HTTP 503 (Service Unavailable).
    """
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Le service est temporairement indisponible. Veuillez réessayer plus tard."
    default_code = 'service_unavailable'


def custom_exception_handler(exc, context):
    """
    Gestionnaire d'exceptions personnalisé global pour l'application REST Framework.

    Intercepte les erreurs (validation Django, non-existence, conflits DB, opérations
    suspectes) pour les traduire en exceptions DRF formatées. Enregistre également
    chaque exception dans le modèle ErrorLog pour un suivi d'audit, avant de renvoyer
    la réponse standardisée au client.

    Args:
        exc (Exception): L'exception levée au cours du traitement de la requête.
        context (dict): Le dictionnaire de contexte fourni par REST Framework, 
                        incluant la requête ('request') et la vue concernée ('view').

    Returns:
        Response: L'objet Response formaté pour le client, ou None si l'exception
                  n'a pu être gérée par DRF (ce qui aboutira à une erreur 500 par Django).
    """
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
            exc = ServiceUnavailableException(detail="Le service de base de données est temporairement indisponible.")

        elif isinstance(exc, SuspiciousOperation):
            exc = DRFValidationError(detail="Requête invalide ou suspecte.")

    except Exception as preprocessing_exc:
        logger.error(f"Erreur lors du prétraitement de l'exception : {preprocessing_exc}")

    response = exception_handler(exc, context)

    request = context.get('request')
    endpoint_url = None
    if request and hasattr(request, 'path'):
        endpoint_url = request.path[:255]

    http_method = None
    if request and hasattr(request, 'method') and request.method in ErrorLog.HttpMethodChoices.values:
        http_method = request.method

    user = None
    try:
        if request and hasattr(request, 'user') and request.user and not request.user.is_anonymous:
            user = request.user
    except Exception:
        pass

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

    if response is not None:
        status_code = response.status_code
        error_message = f"[{status_code}] {str(response.data)[:1000]}"

        if status_code >= 500:
            level = ErrorLog.LogLevels.ERROR
            priority = ErrorLog.LogPriority.HIGH
        else:
            level = ErrorLog.LogLevels.WARNING
            priority = ErrorLog.LogPriority.LOW
    else:
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        error_message = f"{exc.__class__.__name__}: {str(exc)[:1000]}"
        level = ErrorLog.LogLevels.CRITICAL
        priority = ErrorLog.LogPriority.CRITICAL

        response = Response(
            {"detail": "Une erreur interne du serveur est survenue."},
            status=status_code
        )

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
        # Fallback de journalisation en cas d'indisponibilité de la base de données
        logger.critical(
            f"Impossible d'enregistrer le log d'erreur en base de données. Erreur DB: {db_exc} | "
            f"Erreur d'origine: level={level}, message={error_message}, endpoint={endpoint_url}, method={http_method}"
        )

    return response