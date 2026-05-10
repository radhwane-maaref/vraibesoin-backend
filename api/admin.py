from django.contrib import admin
from django.utils import timezone

from api.models import CustomUser, PurchaseIntention, ReflectionQuestion, AppFeedback, ErrorLog

# Register your models here.
@admin.register(CustomUser)
class CustomUserAdmin(admin.ModelAdmin):
    search_fields = ['email', 'first_name', 'last_name']
admin.site.register(PurchaseIntention)
admin.site.register(ReflectionQuestion)
admin.site.register(AppFeedback)
@admin.action(description='Marquer comme TRIAGED')
def mark_as_triaged(modeladmin, request, queryset):
    queryset.update(status=ErrorLog.LogStatus.TRIAGED)

@admin.action(description='Marquer comme FIXED')
def mark_as_fixed(modeladmin, request, queryset):
    queryset.update(status=ErrorLog.LogStatus.FIXED, resolved_at=timezone.now(), is_resolved=True)

@admin.action(description='Marquer comme CLOSED')
def mark_as_closed(modeladmin, request, queryset):
    queryset.update(status=ErrorLog.LogStatus.CLOSED, resolved_at=timezone.now(), is_resolved=True)

class ErrorLogAdmin(admin.ModelAdmin):
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