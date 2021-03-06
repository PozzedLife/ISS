# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ('ISS', '0012_privatemessage'),
    ]

    operations = [
        migrations.AddField(
            model_name='privatemessage',
            name='inbox',
            field=models.ForeignKey(default=0, to=settings.AUTH_USER_MODEL),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='privatemessage',
            name='read',
            field=models.BooleanField(default=False),
        ),
    ]
