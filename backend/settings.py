"""
Configuration principale du projet Django.

Ce module contient toutes les variables de configuration et les paramètres globaux
nécessaires à l'exécution de l'application 'backend'. Il gère l'interfaçage avec la
base de données, les politiques de sécurité (CORS, JWT, SSL), et l'intégration de
services tiers (Celery, Redis, Brevo).
"""

import os
from urllib.parse import urlparse, parse_qsl

import dj_database_url
from datetime import timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


def get_bool_env(name, default=False):
    """
    Extrait et convertit une variable d'environnement en valeur booléenne.

    Args:
        name (str): Le nom de la variable d'environnement.
        default (bool, optional): La valeur par défaut si la variable est absente. Defaults to False.

    Returns:
        bool: True si la valeur de la variable correspond à 'true', '1', 't', 'yes', ou 'y' (insensible à la casse), sinon False.
    """
    return str(os.getenv(name, str(default))).lower() in ('true', '1', 't', 'yes', 'y')


def get_list_env(name, default=''):
    """
    Extrait et convertit une chaîne de caractères séparée par des virgules depuis
    les variables d'environnement en une liste Python.

    Args:
        name (str): Le nom de la variable d'environnement.
        default (str, optional): La valeur par défaut si la variable est absente. Defaults to ''.

    Returns:
        list[str]: La liste des éléments extraits et nettoyés de leurs guillemets.
    """
    val = os.getenv(name, default)
    if not val:
        val = default
    val = val.strip(' "\'')
    return [x.strip(' "\'') for x in val.split(',') if x.strip(' "\'')]


BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv('SECRET_KEY', 'your-fallback-dev-key')

DEBUG = get_bool_env('DEBUG', True)

ALLOWED_HOSTS = get_list_env('ALLOWED_HOSTS', 'localhost,127.0.0.1')
RENDER_EXTERNAL_HOSTNAME = os.getenv('RENDER_EXTERNAL_HOSTNAME')
if RENDER_EXTERNAL_HOSTNAME:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)

MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'corsheaders',
    'api.apps.ApiConfig',
    'django.contrib.postgres',
    # 'silk'
]

MIDDLEWARE = [
    # 'silk.middleware.SilkyMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'backend.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'backend.wsgi.application'

DATABASES = {
    'default': dj_database_url.config(
        default=f"postgres://{os.getenv('DB_USER', 'postgres')}:{os.getenv('DB_PASSWORD', 'postgres')}@{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '5432')}/{os.getenv('DB_NAME', 'postgres')}",
        conn_max_age=600
    )
}

PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.Argon2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher',
    'django.contrib.auth.hashers.BCryptSHA256PasswordHasher',
]

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

LANGUAGE_CODE = 'fr'
TIME_ZONE = 'Africa/Tunis'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}

CORS_ALLOWED_ORIGINS = get_list_env('CORS_ALLOWED_ORIGINS', 'http://localhost:5173')
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = get_list_env('CSRF_TRUSTED_ORIGINS', 'http://localhost:5173')
AUTH_USER_MODEL = 'api.CustomUser'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_THROTTLE_RATES': {
        'password_reset': '2/hour', },
    'EXCEPTION_HANDLER': 'api.exceptions.custom_exception_handler',
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=60),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'AUTH_HEADER_TYPES': ('Bearer',),
}

GOOGLE_OAUTH_CLIENT_ID = os.getenv('GOOGLE_OAUTH_CLIENT_ID')

EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp-relay.brevo.com'
EMAIL_PORT = int(os.getenv('EMAIL_PORT', 2525))
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.getenv('BREVO_SMTP_LOGIN')
EMAIL_HOST_PASSWORD = os.getenv('BREVO_SMTP_PASSWORD')
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'noreply@vrai-besoin.me')
FRONTEND_URL = os.getenv('FRONTEND_URL', 'http://localhost:5173')

SILKY_AUTHENTICATION = False
SILKY_AUTHORISATION = False
SILKY_MAX_REQUEST_BODY_SIZE = 1024
SILKY_MAX_RESPONSE_BODY_SIZE = 1024

if not DEBUG:
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT = get_bool_env('SECURE_SSL_REDIRECT', True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True

REDIS_URL = os.getenv('REDIS_URL')
if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": REDIS_URL,
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
            }
        }
    }

CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', os.getenv('REDIS_URL'))
# Bascule en exécution synchrone si aucun broker n'est configuré
CELERY_TASK_ALWAYS_EAGER = not bool(CELERY_BROKER_URL)
CELERY_TASK_STORE_EAGER_RESULT = not bool(CELERY_BROKER_URL)

CRON_SECRET_KEY = os.getenv('CRON_SECRET_KEY', 'ma-cle-secrete-pour-le-cron-123!')
STATICFILES_DIRS = [
    BASE_DIR / 'static',
]
