# Generated by Django 3.2.7 on 2021-10-20 13:41

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('CollectorAPI', '0004_honeypotrawdata'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='honeypotinfo',
            name='raw_entry',
        ),
    ]