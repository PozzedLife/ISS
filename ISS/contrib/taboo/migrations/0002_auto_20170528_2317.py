# -*- coding: utf-8 -*-
# Generated by Django 1.10 on 2017-05-28 23:17
from __future__ import unicode_literals

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('taboo', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='tabooprofile',
            name='last_registration',
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
        migrations.AlterField(
            model_name='tabooprofile',
            name='mark',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='marked_by', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='tabooprofile',
            name='poster',
            field=models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='taboo_profile', to=settings.AUTH_USER_MODEL),
        ),
    ]
