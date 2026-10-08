from apps.talent.admin import TalentProfileAdmin

SERVER_CONTROLLED = {"is_published", "onboarding_completed_at", "kyc_status"}


def test_lifecycle_fields_are_read_only_in_admin():
    assert set(TalentProfileAdmin.readonly_fields) >= SERVER_CONTROLLED
