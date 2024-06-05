from django.db import models
import random
import hashlib

# Create your models here.


class HoneyPotServer(models.Model):
    name = models.CharField(max_length=32)
    description = models.CharField(max_length=65500)
    ip = models.CharField(max_length=15, unique=True)
    key = models.CharField(max_length=64, unique=True, help_text="Enter 0000 to autogenerate key")
    last_input = models.DateTimeField(auto_now=True)
    isActive = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name}"

    def save(self, *args, **kwargs):
        if self.key == '0000':
            self.key = hashlib.sha256(str(random.getrandbits(256)).encode('utf-8')).hexdigest()
        return super().save(*args, **kwargs)


"""
class ServerKeys(models.Model):
    server = models.ForeignKey(HoneyPotServer, on_delete=models.CASCADE, unique=True)
    key = models.CharField(max_length=64, unique=True, help_text="Enter 0000 to autogenerate key")
    isActive = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.server.name}"

"""


class HoneypotInfo(models.Model):
    server_id = models.ForeignKey(HoneyPotServer, on_delete=models.PROTECT)
    shard_id = models.CharField(max_length=32)
    src_ip = models.CharField(max_length=45)
    dst_ip = models.CharField(max_length=45)
    ip_rep = models.CharField(max_length=128, null=True)
    protocol = models.CharField(max_length=45, null=True)
    type = models.CharField(max_length=128)
    eventid = models.CharField(max_length=128, null=True)
    event_type = models.CharField(max_length=128, null=True)
    countryISO = models.CharField(max_length=4, null=True)
    city_name = models.CharField(max_length=128, null=True)
    region_code = models.CharField(max_length=4, null=True)
    region_name = models.CharField(max_length=128, null=True)
    continent_code = models.CharField(max_length=2, null=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True)
    event_timestamp = models.DateTimeField()
    recieved_timestamp = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("shard_id", "event_timestamp")
        indexes = [models.Index(fields=["event_timestamp"])]


class HoneypotRawData(models.Model):
    record = models.OneToOneField(HoneypotInfo, on_delete=models.PROTECT, to_field='id')
    raw_entry = models.JSONField()


class HoneypotAgregate24hIps(models.Model):
    ip = models.CharField(max_length=45)
    country_iso = models.CharField(max_length=4, null=True)
    count = models.IntegerField()


class HoneypotAgregate7DIps(models.Model):
    ip = models.CharField(max_length=45)
    country_iso = models.CharField(max_length=4, null=True)
    count = models.IntegerField()


class HoneypotAgregate30DIps(models.Model):
    ip = models.CharField(max_length=45)
    country_iso = models.CharField(max_length=4, null=True)
    count = models.IntegerField()


class HoneypotAgregate24hCountry(models.Model):
    country_iso = models.CharField(max_length=4, null=True)
    count = models.IntegerField()


class HoneypotAgregate7DCountry(models.Model):
    country_iso = models.CharField(max_length=4, null=True)
    count = models.IntegerField()


class HoneypotAgregate30DCountry(models.Model):
    country_iso = models.CharField(max_length=4, null=True)
    count = models.IntegerField()


class HoneypotAgregatePerServer(models.Model):
    data_id = models.CharField(max_length=45)
    data = models.JSONField()


class HenoypotInfoHourlyStatisticsAttackIPs(models.Model):
    timestamp = models.DateTimeField()
    src_ip = models.CharField(max_length=45)
    count = models.IntegerField()


class HoneypotReportsStorage(models.Model):
    from_date = models.DateTimeField()
    to_date = models.DateTimeField()
    affected_honeys = models.JSONField()
    countries = models.JSONField()
    data = models.JSONField()

    class Meta:
        unique_together = ("from_date", "to_date")


class PermissionsTable(models.Model):
    from_date = models.DateTimeField()

    class Meta:
        permissions = (("adv_reports", "Може да създава подробни доклади"), ("graphs", "Може да изпозлва Graphs"), ("tables", "Може да използва tables"),)

