from django.contrib import admin
from .models import HoneyPotServer

# Register your models here.


@admin.register(HoneyPotServer)
class HoneyPotServerAdmin(admin.ModelAdmin):
    ordering = ['id']
    list_display = ('name', 'ip', 'isActive', 'last_input')

