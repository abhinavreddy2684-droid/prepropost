import json

import pytest
from django.utils import timezone

from apps.media_library.models import Media
from apps.talent.models import ModerationStatus

from .factories import AvatarSubmissionFactory, GalleryItemFactory, TalentProfileFactory, link_item

pytestmark = pytest.mark.django_db
S = Media.Status
M = ModerationStatus
PRIVATE_FIELDS = {"full_name", "gender", "date_of_birth", "email", "phone"}


def _item(talent, status=S.READY, moderation=M.APPROVED, **kwargs):
    item = GalleryItemFactory(
        talent=talent,
        moderation_status=moderation,
        media__status=status,
        media__ready_at=timezone.now() if status == S.READY else None,
        media__thumbnail_keys={"400": "thumbs/x/400.webp"} if status == S.READY else {},
        **kwargs,
    )
    return item


def _keys(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _keys(value)
    elif isinstance(node, list):
        for value in node:
            yield from _keys(value)


class TestPublicGallery:
    def test_lists_only_public_items_with_signed_urls(self, client):
        talent = TalentProfileFactory(full_name="Secret Person")
        live = _item(talent, position=0)
        live_link = link_item(talent=talent, moderation_status=M.APPROVED, position=1)
        _item(talent, moderation=M.PENDING_REVIEW)
        _item(talent, moderation=M.HIDDEN)
        _item(talent, status=S.PROCESSING)
        _item(talent).soft_delete()

        response = client.get(f"/api/talent/{talent.pk}/gallery")
        assert response.status_code == 200
        body = response.json()
        assert [i["id"] for i in body] == [str(live.id), str(live_link.id)]
        urls = body[0]["urls"]
        assert live.media.object_key in urls["original"]
        assert set(urls["thumbnails"]) == {"400"} and urls["expires_at"]
        assert body[1]["urls"] is None and body[1]["kind"] == "link"
        assert PRIVATE_FIELDS.isdisjoint(_keys(body))
        assert "Secret Person" not in json.dumps(body)
        assert "moderation_note" not in body[0] and "reviewed_by" not in body[0]

    def test_unpublished_or_unknown_talent_is_404(self, client):
        hidden = TalentProfileFactory(is_published=False)
        _item(hidden)
        assert client.get(f"/api/talent/{hidden.pk}/gallery").status_code == 404
        unknown = "00000000-0000-0000-0000-000000000000"
        assert client.get(f"/api/talent/{unknown}/gallery").status_code == 404


class TestSignedUrlExposure:
    def test_owner_gets_urls_for_processed_files_still_in_review(self, auth_client):
        talent = TalentProfileFactory()
        in_review = _item(talent, moderation=M.PENDING_REVIEW)
        processing = _item(talent, status=S.PROCESSING, moderation=M.PENDING_REVIEW)
        api, _ = auth_client(talent.user)
        by_id = {i["id"]: i for i in api.get("/api/talent/me/gallery").json()}
        assert by_id[str(in_review.id)]["urls"]["original"]
        assert by_id[str(processing.id)]["urls"] is None

    def test_discarded_media_gets_no_url(self):
        from apps.media_library.selectors import signed_urls

        item = _item(TalentProfileFactory())
        Media.objects.filter(pk=item.media_id).update(discarded_at=timezone.now())
        item.media.refresh_from_db()
        assert signed_urls(item.media) is None

    def test_public_profile_shows_only_the_approved_avatar(self, client, auth_client):
        talent = TalentProfileFactory()
        approved = AvatarSubmissionFactory(talent=talent, moderation_status=M.APPROVED)
        talent.avatar = approved
        talent.save()
        pending = AvatarSubmissionFactory(talent=talent)  # READY, in review

        public = client.get(f"/api/talent/{talent.pk}").json()
        assert approved.media.object_key in public["avatar"]["original"]
        assert pending.media.object_key not in json.dumps(public)

        api, _ = auth_client(talent.user)
        own = api.get("/api/talent/me/avatar").json()
        assert approved.media.object_key in own["avatar"]["urls"]["original"]
        assert pending.media.object_key in own["submission"]["urls"]["original"]

    def test_profile_without_avatar_has_null_avatar(self, client):
        talent = TalentProfileFactory()
        assert client.get(f"/api/talent/{talent.pk}").json()["avatar"] is None


class TestLikesApi:
    def test_like_and_unlike_are_idempotent(self, auth_client):
        item = _item(TalentProfileFactory())
        api, _ = auth_client()
        url = f"/api/gallery-items/{item.id}/like"
        assert api.post(url).json() == {"item_id": str(item.id), "liked": True, "likes_count": 1}
        assert api.post(url).json()["likes_count"] == 1
        assert api.delete(url).json() == {
            "item_id": str(item.id),
            "liked": False,
            "likes_count": 0,
        }
        assert api.delete(url).json()["likes_count"] == 0

    @pytest.mark.parametrize(
        ("status", "moderation"),
        [(S.READY, M.PENDING_REVIEW), (S.READY, M.HIDDEN), (S.PROCESSING, M.APPROVED)],
    )
    def test_liking_a_non_public_item_is_404(self, auth_client, status, moderation):
        item = _item(TalentProfileFactory(), status=status, moderation=moderation)
        api, _ = auth_client()
        assert api.post(f"/api/gallery-items/{item.id}/like").status_code == 404

    def test_requires_authentication(self, client):
        item = _item(TalentProfileFactory())
        assert client.post(f"/api/gallery-items/{item.id}/like").status_code == 401


def test_endpoints_are_in_the_openapi_schema(client):
    paths = client.get("/api/schema/", HTTP_ACCEPT="application/json").json()["paths"]
    assert "/api/talent/{id}/gallery" in paths
    assert "/api/gallery-items/{id}/like" in paths
