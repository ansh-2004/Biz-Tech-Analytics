from .models import ProviderReceipt


class TemporaryProviderError(Exception):
    pass


class PermanentProviderError(Exception):
    pass


def send_email(job):
    """Local fake provider. It records acceptance; it never sends real mail."""
    receipt = ProviderReceipt.objects.filter(job=job).first()
    if receipt:
        return str(receipt.message_id)
    if job.permanent_failure:
        raise PermanentProviderError("Mock provider rejected the recipient")
    if job.attempts <= job.fail_first:
        raise TemporaryProviderError("Mock provider is temporarily unavailable")
    receipt, _ = ProviderReceipt.objects.get_or_create(job=job)
    return str(receipt.message_id)
