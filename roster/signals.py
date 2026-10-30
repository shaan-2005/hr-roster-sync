from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from .models import Office


@receiver([post_save, post_delete], sender=Office)
def invalidate_office_cache(sender, instance, **kwargs):
    cache.delete(f"office:{instance.tenant_id}:{instance.code}")
