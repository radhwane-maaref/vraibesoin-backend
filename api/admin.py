from django.contrib import admin
from django.utils import timezone

from api.models import CustomUser, PurchaseIntention, ReflectionQuestion, AppFeedback, ErrorLog


@admin.register(CustomUser)
class CustomUserAdmin(admin.ModelAdmin):
    """
    Interface d'administration pour le modèle CustomUser.
    
    Fournit des fonctionnalités de recherche basées sur l'email, 
    le prénom et le nom de famille.
    """
    search_fields = ['email', 'first_name', 'last_name']


admin.site.register(PurchaseIntention)
admin.site.register(ReflectionQuestion)
admin.site.register(AppFeedback)


@admin.action(description='Marquer comme TRIAGED')
def mark_as_triaged(modeladmin, request, queryset):
    """
    Action d'administration pour changer le statut d'un ou plusieurs journaux d'erreurs en 'TRIAGED'.

    Args:
        modeladmin (ModelAdmin): L'instance de la classe ModelAdmin appelant l'action.
        request (HttpRequest): L'objet requête HTTP contenant les informations de la session.
        queryset (QuerySet): La sélection des objets ErrorLog sur lesquels appliquer l'action.

    Returns:
        None
    """
    queryset.update(status=ErrorLog.LogStatus.TRIAGED)


@admin.action(description='Marquer comme FIXED')
def mark_as_fixed(modeladmin, request, queryset):
    """
    Action d'administration pour changer le statut d'un ou plusieurs journaux d'erreurs en 'FIXED'.

    Met également à jour la date de résolution à l'instant présent et 
    marque l'erreur comme résolue.

    Args:
        modeladmin (ModelAdmin): L'instance de la classe ModelAdmin appelant l'action.
        request (HttpRequest): L'objet requête HTTP contenant les informations de la session.
        queryset (QuerySet): La sélection des objets ErrorLog sur lesquels appliquer l'action.

    Returns:
        None
    """
    queryset.update(status=ErrorLog.LogStatus.FIXED, resolved_at=timezone.now(), is_resolved=True)


@admin.action(description='Marquer comme CLOSED')
def mark_as_closed(modeladmin, request, queryset):
    """
    Action d'administration pour changer le statut d'un ou plusieurs journaux d'erreurs en 'CLOSED'.

    Met également à jour la date de résolution à l'instant présent et 
    marque l'erreur comme résolue.

    Args:
        modeladmin (ModelAdmin): L'instance de la classe ModelAdmin appelant l'action.
        request (HttpRequest): L'objet requête HTTP contenant les informations de la session.
        queryset (QuerySet): La sélection des objets ErrorLog sur lesquels appliquer l'action.

    Returns:
        None
    """
    queryset.update(status=ErrorLog.LogStatus.CLOSED, resolved_at=timezone.now(), is_resolved=True)


class ErrorLogAdmin(admin.ModelAdmin):
    """
    Interface d'administration pour le modèle ErrorLog.

    Permet de visualiser, filtrer et gérer les journaux d'erreurs système 
    et applicatives via des actions personnalisées et une organisation 
    par groupes de champs.
    """
    list_display = ('id', 'status', 'priority', 'level', 'endpoint_url', 'assigned_to', 'created_at', 'resolved_at')
    list_filter = ('status', 'priority', 'level', 'http_method', 'assigned_to', 'created_at')
    search_fields = ('error_message', 'endpoint_url', 'stack_trace', 'resolution_note')
    readonly_fields = ('created_at', 'resolved_at')
    actions = [mark_as_triaged, mark_as_fixed, mark_as_closed]
    autocomplete_fields = ['assigned_to', 'user']

    fieldsets = (
        ('Overview', {
            'fields': ('level', 'status', 'priority', 'assigned_to', 'user')
        }),
        ('Error Details', {
            'fields': ('error_message', 'stack_trace', 'endpoint_url', 'http_method')
        }),
        ('Resolution', {
            'fields': ('resolution_note', 'is_resolved', 'resolved_at', 'created_at')
        }),
    )

admin.site.register(ErrorLog, ErrorLogAdmin)