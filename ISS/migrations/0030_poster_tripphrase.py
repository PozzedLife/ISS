# -*- coding: utf-8 -*-
# Generated by Django 1.10 on 2017-04-29 22:46
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ISS', '0029_post_posted_from'),
    ]

    operations = [
        migrations.AddField(
            model_name='poster',
            name='tripphrase',
            field=models.CharField(max_length=256, null=True),
        ),
    ]
