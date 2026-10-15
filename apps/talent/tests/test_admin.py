import pytest

from apps.media_library.admin import MediaAdmin
from apps.talent.admin import AvatarSubmissionAdmin, GalleryItemAdmin, TalentProfileAdmin

SERVER_CONTROLLED = {"is_published", "onboarding_completed_at", "kyc_status", "avatar"}


def test_lifecycle_fields_are_read_only_in_admin():
    assert set(TalentProfileAdmin.readonly_fields) >= SERVER_CONTROLLED


@pytest.mark.parametrize("model_admin", [GalleryItemAdmin, AvatarSubmissionAdmin, MediaAdmin])
def test_media_and_moderation_admins_cannot_change_state(model_admin):
    from django.contrib import admin as django_admin

    instance = model_admin(model_admin_model(model_admin), django_admin.site)
    readonly = set(instance.get_readonly_fields(request=None))
    assert {"status", "moderation_status"} & {
        f.name for f in instance.model._meta.fields
    } <= readonly
    assert not instance.has_add_permission(request=None)
    assert not instance.has_delete_permission(request=None)
    assert not instance.actions or instance.actions == []


def model_admin_model(model_admin):
    from django.contrib import admin as django_admin

    return next(m for m, a in django_admin.site._registry.items() if type(a) is model_admin)
