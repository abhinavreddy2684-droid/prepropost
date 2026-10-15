# M2 media reshape, step 2 of 3: drop the pre-M2 Media and MediaLike tables. Gallery metadata and
# likes move to talent (GalleryItem, GalleryItemLike); 0003 recreates Media as the byte lifecycle.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('media_library', '0001_initial'),
        ('talent', '0002_remove_talentprofile_avatar'),
    ]

    operations = [
        migrations.DeleteModel(name='MediaLike'),
        migrations.DeleteModel(name='Media'),
    ]
