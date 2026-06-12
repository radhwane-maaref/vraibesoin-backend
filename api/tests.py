from unittest.mock import patch, MagicMock
from django.test import TestCase
from django.core.exceptions import (
    ValidationError as DjangoValidationError,
    ObjectDoesNotExist,
    PermissionDenied as DjangoPermissionDenied,
)
from django.http import Http404
from django.db import IntegrityError, OperationalError
from rest_framework import status
from rest_framework.exceptions import (
    ValidationError as DRFValidationError,
    NotFound as DRFNotFound,
    PermissionDenied as DRFPermissionDenied,
)
from api.exceptions import (
    custom_exception_handler,
    DatabaseConflictException,
    ServiceUnavailableException,
)
from api.models import ErrorLog


class CustomExceptionHandlerTests(TestCase):
    def setUp(self):
        # Prepare a mock request and context
        self.mock_request = MagicMock()
        self.mock_request.path = "/api/test-endpoint/"
        self.mock_request.method = "POST"
        self.mock_request.user = MagicMock()
        self.mock_request.user.is_anonymous = False
        self.context = {"request": self.mock_request}

    @patch("api.models.ErrorLog.objects.create")
    def test_django_validation_error_mapping(self, mock_create):
        # A Django validation error with a dictionary mapping fields to list of errors
        django_err = DjangoValidationError({"field_name": ["Invalid value."]})

        response = custom_exception_handler(django_err, self.context)

        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("field_name", response.data)

        # Verify it logged an ErrorLog
        mock_create.assert_called_once()
        log_kwargs = mock_create.call_args[1]
        self.assertEqual(log_kwargs["level"], ErrorLog.LogLevels.WARNING)
        self.assertEqual(log_kwargs["priority"], ErrorLog.LogPriority.LOW)

    @patch("api.models.ErrorLog.objects.create")
    def test_django_validation_error_non_field_mapping(self, mock_create):
        # A Django validation error with __all__ or generic messages
        django_err = DjangoValidationError({"__all__": ["Generic validation error."]})

        response = custom_exception_handler(django_err, self.context)

        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("non_field_errors", response.data)

        mock_create.assert_called_once()

    @patch("api.models.ErrorLog.objects.create")
    def test_object_does_not_exist_mapping(self, mock_create):
        exc = ObjectDoesNotExist("Object not found in DB")
        response = custom_exception_handler(exc, self.context)

        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data["detail"], "La ressource demandée n'existe pas.")
        mock_create.assert_called_once()

    @patch("api.models.ErrorLog.objects.create")
    def test_permission_denied_mapping(self, mock_create):
        exc = DjangoPermissionDenied("No permission")
        response = custom_exception_handler(exc, self.context)

        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["detail"], "Vous n'avez pas l'autorisation d'effectuer cette action.")
        mock_create.assert_called_once()

    @patch("api.models.ErrorLog.objects.create")
    def test_integrity_error_unique_constraint(self, mock_create):
        exc = IntegrityError("duplicate key value violates unique constraint")
        response = custom_exception_handler(exc, self.context)

        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["detail"], "Une ressource avec ces données existe déjà.")
        mock_create.assert_called_once()

    @patch("api.models.ErrorLog.objects.create")
    def test_integrity_error_other_constraint(self, mock_create):
        exc = IntegrityError("violates check constraint 'chk_some_constraint'")
        response = custom_exception_handler(exc, self.context)

        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["detail"], "La requête enfreint une contrainte de validation de la base de données.")
        mock_create.assert_called_once()

    @patch("api.models.ErrorLog.objects.create")
    def test_operational_error_mapping(self, mock_create):
        exc = OperationalError("connection refused")
        response = custom_exception_handler(exc, self.context)

        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data["detail"], "Le service de base de données est temporairement indisponible.")
        mock_create.assert_called_once()

    @patch("api.models.ErrorLog.objects.create")
    def test_unhandled_python_exception_mapping(self, mock_create):
        exc = ValueError("Some internal programming error")
        response = custom_exception_handler(exc, self.context)

        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertEqual(response.data["detail"], "Une erreur interne du serveur est survenue.")

        mock_create.assert_called_once()
        log_kwargs = mock_create.call_args[1]
        self.assertEqual(log_kwargs["level"], ErrorLog.LogLevels.CRITICAL)
        self.assertEqual(log_kwargs["priority"], ErrorLog.LogPriority.CRITICAL)

    @patch("api.exceptions.logger")
    @patch("api.models.ErrorLog.objects.create")
    def test_resilient_logging_fallback(self, mock_create, mock_logger):
        # Force database logging to fail (e.g. database connection lost)
        mock_create.side_effect = Exception("DB Connection Lost")
        exc = ValueError("Some bug")

        response = custom_exception_handler(exc, self.context)

        # Exception handler should still return 500 response without crashing itself
        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)

        # Verify fallback logger was called to record the critical failure
        mock_logger.critical.assert_called_once()
