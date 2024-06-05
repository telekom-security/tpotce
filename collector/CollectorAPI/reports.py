from datetime import datetime, timedelta, date
from time import gmtime, strftime, mktime
import json
import pytz
from django.db import connection

import CollectorAPI.models
import CollectorAPI.models as CollectorDB
from django.db.models import Count


def gen_report(from_date, to_date, affected_honeys, countries, with_data=0):
    from_date = datetime.combine(from_date, datetime.min.time())
    from_date = from_date.replace(minute=0, hour=0, second=0, microsecond= 0)
    date_from = pytz.utc.localize(from_date)
    to_date = datetime.combine(to_date, datetime.min.time())
    to_date = to_date.replace(hour=23, minute=59, second=59, microsecond=99999)
    date_to = pytz.utc.localize(to_date)

    attack = CollectorAPI.models.HoneypotInfo.objects.values('src_ip').annotate(icount=Count('src_ip')).\
        order_by('icount').filter(event_timestamp__gte=date_from, event_timestamp__lte=date_to,
                                  server_id_id__in=affected_honeys)
    if 'all' not in countries:
        attack = attack.filter(countryISO__in=countries)

    attackers = []
    for attacker in attack:
        print(attacker['src_ip'])
        if with_data == 1:
            attackers.append([attacker['src_ip'], attacker['icount']])
        else:
            attackers.append(attacker['src_ip'])

    report = CollectorAPI.models.HoneypotReportsStorage.objects.filter(from_date=date_from, to_date=date_to).exists()
    if report:
        report = CollectorAPI.models.HoneypotReportsStorage.objects.filter(from_date=date_from, to_date=date_to)
        report = report[0]

    else:
    #let's save to db
        report = CollectorAPI.models.HoneypotReportsStorage()
        report.from_date = date_from
        report.to_date = date_to
        report.affected_honeys = json.dumps(affected_honeys)
        report.countries = json.dumps(countries)
        report.data = json.dumps(attackers)

        report.save()
    return report.data

