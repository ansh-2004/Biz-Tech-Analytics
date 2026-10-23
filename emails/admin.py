from django.contrib import admin

from .models import EmailAttempt, EmailJob, ProviderReceipt


@admin.register(EmailJob)
class EmailJobAdmin(admin.ModelAdmin):
    list_display = ("id", "recipient", "status", "attempts", "failures", "created_at")
    list_filter = ("status",)
    search_fields = ("=id", "=recipient")
    readonly_fields = ("id", "created_at", "sent_at")
    show_full_result_count = False


@admin.register(EmailAttempt)
class EmailAttemptAdmin(admin.ModelAdmin):
    list_display = ("job_id", "number", "outcome", "retry_delay", "started_at")
    list_filter = ("outcome",)


admin.site.register(ProviderReceipt)
