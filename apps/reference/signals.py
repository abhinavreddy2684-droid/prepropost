from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import City, Craft, State, TalentType
from .services import invalidate_bootstrap_cache


@receiver([post_save, post_delete], sender=Craft)
@receiver([post_save, post_delete], sender=TalentType)
@receiver([post_save, post_delete], sender=State)
@receiver([post_save, post_delete], sender=City)
def _reference_changed(sender, **kwargs):
    invalidate_bootstrap_cache()
