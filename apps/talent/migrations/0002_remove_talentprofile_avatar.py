# M2 media reshape, step 1 of 3: detach the old avatar FK so the old Media table can be dropped.
# Schema only: existing avatars and media rows are not carried over (nothing is in production).

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('talent', '0001_initial'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='talentprofile',
            name='avatar',
        ),
    ]
