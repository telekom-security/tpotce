from datetime import datetime, timedelta
import json
import pytz
from django.conf import settings

from django.shortcuts import render, redirect
import sys
from django.views.decorators.csrf import csrf_exempt
import CollectorAPI.models as CollectorDB
from django.db import connection
# Create your views here.

from django.http import HttpResponse


# We need to disable CSRF as we are not using it.
@csrf_exempt
def post(request):
    """
    URL end point: /API/post
    Base post function.
    Requires:
    POST request
    Authorization Header with type "Token xxxxxxx"
    File containing the JSON
    """
    if request.method != 'POST':
        return HttpResponse('Only post method accepted')

    if 'Authorization' not in request.headers:
        return HttpResponse('Authorization token required')

    token = request.headers['Authorization']
    token = token.split()
    if token[0] != 'Token':
        return HttpResponse('What are you trying to pull?')

    server = CollectorDB.HoneyPotServer.objects.filter(key=token[1])

    if not server:
        return HttpResponse('Invalid key ...')

    if not server[0].isActive:
        return HttpResponse('Invalid key ...')

    if server[0].ip != get_client_ip(request):
        return HttpResponse('Invalid key ...')

    for key, file in request.FILES.items():
        tmp_file = file.file
        json_data = ''
        try:
            json_data = json.loads(tmp_file.read())
        except:
            print('Error loading JSON file from ', server[0])

        if post_read_json_and_store(json_data, server[0]):
            server[0].save()

    print(server[0].name, file=sys.stderr)
    return HttpResponse("200 OK")


@csrf_exempt
def post_local(request):
    """
    URL end point: /API/post_local
    Base post function.
    Requires:
    POST request
    Authorization Header with type "IP xxxxxxx"
    File containing the JSON
    """
    if request.method != 'POST':
        return HttpResponse('Only post method accepted')

    if 'Authorization' not in request.headers:
        return HttpResponse('Authorization token required')

    token = request.headers['Authorization']
    token = token.split()
    if token[0] != 'IP':
        return HttpResponse('What are you trying to pull?')

    server = CollectorDB.HoneyPotServer.objects.filter(ip__exact=token[1])

    if not server:
        return HttpResponse('Invalid key ...')

    if not server[0].isActive:
        return HttpResponse('Invalid key ...')

    if get_client_ip(request) != '127.0.0.1':
        return HttpResponse('Invalid key ...')

    for key, file in request.FILES.items():
        tmp_file = file.file
        json_data = ''
        try:
            json_data = json.loads(tmp_file.read())
        except:
            print('Error loading JSON file from ', server[0])

        if post_read_json_and_store(json_data, server[0]):
            server[0].save()

    print(server[0].name, file=sys.stderr)
    return HttpResponse("200 OK")


def get_targets(request):
    if get_client_ip(request) != '127.0.0.1':
        return HttpResponse('Invalid key ...')
    servers = CollectorDB.HoneyPotServer.objects.filter(isActive=True)
    response = []
    for server in servers:
        response.append(server.ip)

    response = ', '.join(response)
    get_from_time(request)
    return HttpResponse(json.dumps(response), content_type="application/json")


def get_from_time(request):
    if get_client_ip(request) != '127.0.0.1':
        return HttpResponse('Invalid key ...')
    now = datetime.now()
    if now.minute < 15:
        from_time = now.replace(minute=45, second=0, microsecond=0) - timedelta(hours=1)
    elif now.minute < 30:
        from_time = now.replace(minute=0, second=0, microsecond=0)
    elif now.minute < 45:
        from_time = now.replace(minute=15, second=0, microsecond=0)
    else:
        from_time = now.replace(minute=30, second=0, microsecond=0)
    from_time = from_time.strftime("%Y-%m-%dT%H:%M:%S")

    return HttpResponse(from_time)


def get_to_time(request):
    if get_client_ip(request) != '127.0.0.1':
        return HttpResponse('Invalid key ...')
    now = datetime.now()
    if now.minute < 15:
        from_to = now.replace(minute=59, second=59, microsecond=999999) - timedelta(hours=1)
    elif now.minute < 30:
        from_to = now.replace(minute=14, second=59, microsecond=999999)
    elif now.minute < 45:
        from_to = now.replace(minute=29, second=59, microsecond=999999)
    else:
        from_to = now.replace(minute=44, second=59, microsecond=999999)
    from_to = from_to.strftime("%Y-%m-%dT%H:%M:%S")

    return HttpResponse(from_to)


def get_attack_ips_json(request):
    """
    Returns the URL /API/report/ips
    Request accepts:
        days = int
        limit = int
        ISO = str
        no_count -> If set returns only IPs
    :param request:
    :return:
    """
    if not request.user.is_authenticated:
        return redirect('%s?next=%s' % (settings.LOGIN_URL, request.path))

    days = 1
    limit = 10
    ISO = 'any'
    no_count = True
    show_iso = False
    if 'no_count' in request.GET:
        no_count = False

    if 'days' in request.GET:
        days = int(request.GET['days'])

    if 'limit' in request.GET:
        limit = int(request.GET['limit'])

    if 'iso' in request.GET:
        ISO = request.GET['iso']
        ISO = ISO.upper()

    if 'show_iso' in request.GET:
        show_iso = True

    if ISO == 'any' and days in [1, 7, 30]:
        attacks = gen_agregated_ips(days, limit, no_count, show_iso)
    else:
        attacks = gen_attack_ips(days, limit, ISO, no_count, show_iso)
    return HttpResponse(json.dumps(attacks), content_type="application/json")


def get_attack_countries_json(request):
    if not request.user.is_authenticated:
        return redirect('%s?next=%s' % (settings.LOGIN_URL, request.path))
    days = 1
    limit = 10
    no_count = True
    if 'no_count' in request.GET:
        no_count = False

    if 'days' in request.GET:
        days = int(request.GET['days'])

    if 'limit' in request.GET:
        limit = int(request.GET['limit'])

    if days in [1, 7, 30]:
        attacks = gen_agregated_countries(days, limit, no_count)
    else:
        attacks = gen_attack_countries(days, limit, no_count)
    return HttpResponse(json.dumps(attacks), content_type="application/json")


def get_protocols_json(request):
    if not request.user.is_authenticated:
        return redirect('%s?next=%s' % (settings.LOGIN_URL, request.path))
    days = 1
    limit = 10
    no_count = True
    if 'no_count' in request.GET:
        no_count = False

    if 'days' in request.GET:
        days = int(request.GET['days'])

    if 'limit' in request.GET:
        limit = int(request.GET['limit'])

    attacks = gen_protocols(days, limit, no_count)
    return HttpResponse(json.dumps(attacks), content_type="application/json")


def get_type_per_server_json(request):
    if not request.user.is_authenticated:
        return redirect('%s?next=%s' % (settings.LOGIN_URL, request.path))
    days = 0
    if 'days' in request.GET:
        days = int(request.GET['days'])
    iso = 'any'
    if 'iso' in request.GET:
        iso = request.GET['iso']

    if days in [1, 7, 30] and iso in ['any', 'bg', 'BG']:
        types_per_server = gen_agregated_per_server(days, iso)
    else:
        types_per_server = json.dumps(gen_type_per_server(days, iso), sort_keys=True)

    return HttpResponse(json.dumps(types_per_server), content_type="application/json")


def agregate_per_server_24h(request):
    load_data = gen_type_per_server(1)
    try:
        data = CollectorDB.HoneypotAgregatePerServer.objects.get(data_id='24h_all')
    except CollectorDB.HoneypotAgregatePerServer.DoesNotExist:
        data = CollectorDB.HoneypotAgregatePerServer()
        data.data_id = '24h_all'

    data.data = load_data
    data.save()
    output = ['OK']
    return HttpResponse(json.dumps(output), content_type="application/json")


def agregate_per_server_7d(request):
    load_data = gen_type_per_server(7)
    try:
        data = CollectorDB.HoneypotAgregatePerServer.objects.get(data_id='7d_all')
    except CollectorDB.HoneypotAgregatePerServer.DoesNotExist:
        data = CollectorDB.HoneypotAgregatePerServer()
        data.data_id = '7d_all'

    data.data = load_data
    data.save()
    output = ['OK']
    return HttpResponse(json.dumps(output), content_type="application/json")


def agregate_per_server_30d(request):
    load_data = gen_type_per_server(30)
    try:
        data = CollectorDB.HoneypotAgregatePerServer.objects.get(data_id='30d_all')
    except CollectorDB.HoneypotAgregatePerServer.DoesNotExist:
        data = CollectorDB.HoneypotAgregatePerServer()
        data.data_id = '30d_all'

    data.data = load_data
    data.save()
    output = ['OK']
    return HttpResponse(json.dumps(output), content_type="application/json")


def agregate_per_server_bg_24h(request):
    load_data = gen_type_per_server(1, 'bg')
    try:
        data = CollectorDB.HoneypotAgregatePerServer.objects.get(data_id='24h_bg')
    except CollectorDB.HoneypotAgregatePerServer.DoesNotExist:
        data = CollectorDB.HoneypotAgregatePerServer()
        data.data_id = '24h_bg'

    data.data = load_data
    data.save()
    output = ['OK']
    return HttpResponse(json.dumps(output), content_type="application/json")


def agregate_per_server_bg_7d(request):
    load_data = gen_type_per_server(7, 'bg')
    try:
        data = CollectorDB.HoneypotAgregatePerServer.objects.get(data_id='7d_bg')
    except CollectorDB.HoneypotAgregatePerServer.DoesNotExist:
        data = CollectorDB.HoneypotAgregatePerServer()
        data.data_id = '7d_bg'

    data.data = load_data
    data.save()
    output = ['OK']
    return HttpResponse(json.dumps(output), content_type="application/json")


def agregate_per_server_bg_30d(request):
    load_data = gen_type_per_server(30, 'bg')
    try:
        data = CollectorDB.HoneypotAgregatePerServer.objects.get(data_id='30d_bg')
    except CollectorDB.HoneypotAgregatePerServer.DoesNotExist:
        data = CollectorDB.HoneypotAgregatePerServer()
        data.data_id = '30d_bg'

    data.data = load_data
    data.save()
    output = ['OK']
    return HttpResponse(json.dumps(output), content_type="application/json")

def agregate_ip_24h(request):
    load_data = gen_attack_ips(1, 0, 'any', True, True)
    CollectorDB.HoneypotAgregate24hIps.objects.all().delete()
    query = 'ALTER SEQUENCE \"CollectorAPI_honeypotagregate24hips_id_seq\" RESTART WITH 1;'
    with connection.cursor() as cursor:
        cursor.execute(query)
    for data in load_data:
        entry = CollectorDB.HoneypotAgregate24hIps()
        entry.ip = data[0]
        entry.country_iso = data[1]
        entry.count = data[2]
        entry.save()
    output = ['OK']
    return HttpResponse(json.dumps(output), content_type="application/json")


def agregate_ip_bg_24h(request):
    load_data = gen_attack_ips(1, 0, 'BG', True, True)
    CollectorDB.HoneypotAgregate24hBGIps.objects.all().delete()
    query = 'ALTER SEQUENCE \"CollectorAPI_honeypotagregate24hips_id_seq\" RESTART WITH 1;'
    with connection.cursor() as cursor:
        cursor.execute(query)
    for data in load_data:
        entry = CollectorDB.HoneypotAgregate24hBGIps()
        entry.ip = data[0]
        entry.country_iso = data[1]
        entry.count = data[2]
        entry.save()
    output = ['OK']
    return HttpResponse(json.dumps(output), content_type="application/json")


def agregate_ip_7d(request):
    load_data = gen_attack_ips(7, 0, 'any', True, True)
    CollectorDB.HoneypotAgregate7DIps.objects.all().delete()
    query = 'ALTER SEQUENCE \"CollectorAPI_honeypotagregate7dips_id_seq\" RESTART WITH 1;'
    with connection.cursor() as cursor:
        cursor.execute(query)
    for data in load_data:
        entry = CollectorDB.HoneypotAgregate7DIps()
        entry.ip = data[0]
        entry.country_iso = data[1]
        entry.count = data[2]
        entry.save()
    output = ['OK']
    return HttpResponse(json.dumps(output), content_type="application/json")


def agregate_ip_bg_7d(request):
    load_data = gen_attack_ips(7, 0, 'BG', True, True)
    CollectorDB.HoneypotAgregate7DIBGps.objects.all().delete()
    query = 'ALTER SEQUENCE \"CollectorAPI_honeypotagregate7dibgps_id_seq\" RESTART WITH 1;'
    with connection.cursor() as cursor:
        cursor.execute(query)
    for data in load_data:
        entry = CollectorDB.HoneypotAgregate7DIBGps()
        entry.ip = data[0]
        entry.country_iso = data[1]
        entry.count = data[2]
        entry.save()
    output = ['OK']
    return HttpResponse(json.dumps(output), content_type="application/json")


def agregate_ip_30d(request):
    load_data = gen_attack_ips(30, 0, 'any', True, True)
    CollectorDB.HoneypotAgregate30DIps.objects.all().delete()
    query = 'ALTER SEQUENCE \"CollectorAPI_honeypotagregate30dips_id_seq\" RESTART WITH 1;'
    with connection.cursor() as cursor:
        cursor.execute(query)
    for data in load_data:
        entry = CollectorDB.HoneypotAgregate30DIps()
        entry.ip = data[0]
        entry.country_iso = data[1]
        entry.count = data[2]
        entry.save()
    output = ['OK']
    return HttpResponse(json.dumps(output), content_type="application/json")


def agregate_ip_bg_30d(request):
    load_data = gen_attack_ips(30, 0, 'BG', True, True)
    CollectorDB.HoneypotAgregate30DBGIps.objects.all().delete()
    query = 'ALTER SEQUENCE \"CollectorAPI_honeypotagregate30dbgips_id_seq\" RESTART WITH 1;'
    with connection.cursor() as cursor:
        cursor.execute(query)
    for data in load_data:
        entry = CollectorDB.HoneypotAgregate30DBGIps()
        entry.ip = data[0]
        entry.country_iso = data[1]
        entry.count = data[2]
        entry.save()
    output = ['OK']
    return HttpResponse(json.dumps(output), content_type="application/json")


def agregate_country_24h(request):
    load_data = gen_attack_countries(1, 0)
    CollectorDB.HoneypotAgregate24hCountry.objects.all().delete()
    query = 'ALTER SEQUENCE \"CollectorAPI_honeypotagregate24hcountry_id_seq\" RESTART WITH 1;'
    with connection.cursor() as cursor:
        cursor.execute(query)
    for data in load_data:
        entry = CollectorDB.HoneypotAgregate24hCountry()
        entry.country_iso = data[0]
        entry.count = data[1]
        entry.save()
    output = ['OK']
    return HttpResponse(json.dumps(output), content_type="application/json")


def agregate_country_7d(request):
    load_data = gen_attack_countries(7, 0)
    CollectorDB.HoneypotAgregate7DCountry.objects.all().delete()
    query = 'ALTER SEQUENCE \"CollectorAPI_honeypotagregate7dcountry_id_seq\" RESTART WITH 1;'
    with connection.cursor() as cursor:
        cursor.execute(query)
    for data in load_data:
        entry = CollectorDB.HoneypotAgregate7DCountry()
        entry.country_iso = data[0]
        entry.count = data[1]
        entry.save()
    output = ['OK']
    return HttpResponse(json.dumps(output), content_type="application/json")


def agregate_country_30d(request):
    load_data = gen_attack_countries(30, 0)
    CollectorDB.HoneypotAgregate30DCountry.objects.all().delete()
    query = 'ALTER SEQUENCE \"CollectorAPI_honeypotagregate30dcountry_id_seq\" RESTART WITH 1;'
    with connection.cursor() as cursor:
        cursor.execute(query)
    for data in load_data:
        entry = CollectorDB.HoneypotAgregate30DCountry()
        entry.country_iso = data[0]
        entry.count = data[1]
        entry.save()
    output = ['OK']
    return HttpResponse(json.dumps(output), content_type="application/json")


def sync_missing_info(request):
    missing = CollectorDB.HoneypotInfo.objects.filter(countryISO__isnull=False, longitude__isnull=True).order_by('id')\
                  .all()[:50000]
    for p in missing:
        hit = p.raw_entry
        if 'geoip' in hit['_source']:
            if 'city_name' in hit['_source']['geoip']:
                p.city_name = hit['_source']['geoip']['city_name']
            if 'region_code' in hit['_source']['geoip']:
                p.region_code = hit['_source']['geoip']['region_code']
            if 'region_name' in hit['_source']['geoip']:
                p.region_name = hit['_source']['geoip']['region_name']
            if 'continent_code' in hit['_source']['geoip']:
                p.continent_code = hit['_source']['geoip']['continent_code']
            if 'latitude' in hit['_source']['geoip']:
                p.latitude = hit['_source']['geoip']['latitude']
            if 'longitude' in hit['_source']['geoip']:
                p.longitude = hit['_source']['geoip']['longitude']
        p.save()
    output = ['OK']
    return HttpResponse(json.dumps(output), content_type="application/json")


def post_read_json_and_store(json_data, server_id):
    """
    Helper function that should read JSON line by line and set it up in DB
    :param json_data:
    :param server_id:
    :return boolean:
    """
    if 'hits' not in json_data:
        return False
    skipped = 0
    for hit in json_data['hits']['hits']:
        # If SRC ip is the recieving server - ignore it.
        if hit['_source']['src_ip'] == "83.228.89.253":
            skipped = skipped + 1
            continue
        if hit['_source']['src_ip'] == "78.83.66.168":
            skipped = skipped + 1
            continue
        if hit['_source']['src_ip'] == "8.8.8.8":
            skipped = skipped + 1
            continue
        if hit['_source']['src_ip'] == hit['_source']['t-pot_ip_int']:
            skipped = skipped + 1
            continue
        db_hit = CollectorDB.HoneypotInfo()
        db_hit.server_id = server_id
        db_hit.shard_id = hit['_id']
        db_hit.src_ip = hit['_source']['src_ip']
        if 'ip_rep' in hit['_source']:
            db_hit.ip_rep = hit['_source']['ip_rep']
        try:
            db_hit.dst_ip = hit['_source']['t-pot_ip_ext']
        except:
            print('There was error with Honey pot IP for the following record record')
            print(hit['_source'])
            print('Skipping')
            continue

        db_hit.type = hit['_source']['type']
        if 'protocol' in hit['_source']:
            db_hit.protocol = hit['_source']['protocol']
        if 'eventid' in hit['_source']:
            db_hit.eventid = hit['_source']['eventid']
        if 'event_type' in hit['_source']:
            db_hit.event_type = hit['_source']['event_type']
        if 'geoip' in hit['_source']:
            # print(hit['_source']['geoip'])
            if 'country_code2' in hit['_source']['geoip']:
                # print(hit['_source']['geoip']['country_code2'])
                db_hit.countryISO = hit['_source']['geoip']['country_code2']
            if 'city_name' in hit['_source']['geoip']:
                # print(hit['_source']['geoip']['city_name'])
                db_hit.city_name = hit['_source']['geoip']['city_name']
            if 'region_code' in hit['_source']['geoip']:
                # print(hit['_source']['geoip']['region_code'])
                db_hit.region_code = hit['_source']['geoip']['region_code']
            if 'region_name' in hit['_source']['geoip']:
                # print(hit['_source']['geoip']['region_name'])
                db_hit.region_name = hit['_source']['geoip']['region_name']
            if 'continent_code' in hit['_source']['geoip']:
                db_hit.continent_code = hit['_source']['geoip']['continent_code']
            if 'latitude' in hit['_source']['geoip']:
                # print(hit['_source']['geoip']['latitude'])
                db_hit.latitude = hit['_source']['geoip']['latitude']
            if 'longitude' in hit['_source']['geoip']:
                # print(hit['_source']['geoip']['longitude'])
                db_hit.longitude = hit['_source']['geoip']['longitude']

        hit['_source']['timestamp'] = hit['_source']['timestamp'].replace('+0000', 'Z')
        if hit['_source']['timestamp'][19] != '.':
            datetimeOBJ = datetime.strptime(hit['_source']['timestamp'], '%Y-%m-%dT%H:%M:%SZ')
        elif hit['_source']['timestamp'][-1] == 'Z':
            datetimeOBJ = datetime.strptime(hit['_source']['timestamp'], '%Y-%m-%dT%H:%M:%S.%fZ')
        else:
            datetimeOBJ = datetime.strptime(hit['_source']['timestamp'], '%Y-%m-%dT%H:%M:%S.%f')

        datetimeOBJ = pytz.utc.localize(datetimeOBJ)
        db_hit.event_timestamp = datetimeOBJ
        db_hit.recieved_timestamp = datetime.now()
        db_hit.raw_entry = hit
        try:
            db_hit.save()
            raw_data = CollectorDB.HoneypotRawData()
            raw_data.record_id = db_hit.id
            raw_data.raw_entry = hit
            try:
                raw_data.save()
            except:
                print('Issue with raw data at ' + hit['_source']['eventid'])
        except:
            print('Duplicate entry found, Skipping')

    print('Entries skipped ' + str(skipped))
    return True


def rolling_update(self):
    hour_from = datetime.now() - timedelta(hours=1)
    hour_from = hour_from.replace(minute=0, second=0, microsecond=0)
    hour_to = hour_from.replace(minute=59, second=59, microsecond=999999)
    hour_from = pytz.utc.localize(hour_from)
    hour_to = pytz.utc.localize(hour_to)
    print(hour_from)


def get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


def gen_agregated_ips(time_limit=1, count_limit=0, show_count=True, show_iso=False):
    params = []
    query_attack = 'SELECT DISTINCT ip'
    if show_iso:
        query_attack = query_attack + ', \"country_iso\"'
    if show_count:
        query_attack = query_attack + ', count'

    query_attack = query_attack + ' FROM'
    if time_limit == 1:
        query_attack = query_attack + ' \"CollectorAPI_honeypotagregate24hips\"'
    elif time_limit == 7:
        query_attack = query_attack + ' \"CollectorAPI_honeypotagregate7dips\"'
    elif time_limit == 30:
        query_attack = query_attack + ' \"CollectorAPI_honeypotagregate30dips\"'
    else:
        raise Exception('Invalid Range')
    if show_count:
        query_attack = query_attack + ' ORDER BY count DESC'
    if count_limit > 0:
        query_attack = query_attack + ' LIMIT %s'
        params.append(count_limit)

    with connection.cursor() as cursor:
        cursor.execute(query_attack, params)
        attack = cursor.fetchall()
        print(connection.queries)
    print("debug")
    print(attack)
    return attack


def gen_attack_ips(time_limit=0, count_limit=0, iso='any', show_count=True, show_iso=False, show_city=False):
    """
    :param time_limit: -> time in days
    :param count_limit: -> How many entries to get
    :param iso:         -> country ISO
    :param show_count:  -> Should it get count
    :return: Array
    """
    params = []
    query_attack = 'SELECT DISTINCT src_ip'
    if show_iso:
        query_attack = query_attack + ', \"countryISO\"'
    if show_city:
        query_attack = query_attack + ', city_name'
    if show_count:
        query_attack = query_attack + ', COUNT(src_ip) as count'

    query_attack = query_attack + ' FROM \"CollectorAPI_honeypotinfo\" '
    if time_limit > 0 or iso != 'any':
        query_attack = query_attack + ' WHERE'
    if time_limit > 0:
        date_from = datetime.now() - timedelta(days=time_limit)
        date_from = pytz.utc.localize(date_from)
        params.append(date_from)
        query_attack = query_attack + ' event_timestamp > %s'

    if iso != 'any' and iso.isalnum():
        if time_limit > 0:
            query_attack = query_attack + ' AND'
        query_attack = query_attack + ' \"countryISO\" LIKE %s'
        params.append(iso.upper())

    if show_count:
        query_attack = query_attack + ' GROUP BY src_ip'

    if show_iso:
        query_attack = query_attack + ', \"countryISO\"'
    if show_city:
        query_attack = query_attack + ', city_name'

    if show_count:
        query_attack = query_attack + ' ORDER BY count DESC'
    if count_limit > 0:
        query_attack = query_attack + ' LIMIT %s'
        params.append(count_limit)

    with connection.cursor() as cursor:
        cursor.execute(query_attack, params)
        attack = cursor.fetchall()

    return attack


def gen_agregated_countries(time_limit=0, count_limit=0, show_count=True):
    params = []
    query_countries = 'SELECT DISTINCT \"country_iso\"'
    if show_count:
        query_countries = query_countries + ', count'

    if time_limit == 1:
        query_countries = query_countries + ' FROM \"CollectorAPI_honeypotagregate24hcountry\"'
    elif time_limit == 7:
        query_countries = query_countries + ' FROM \"CollectorAPI_honeypotagregate7dcountry\"'
    elif time_limit == 30:
        query_countries = query_countries + ' FROM \"CollectorAPI_honeypotagregate30dcountry\"'
    else:
        raise Exception('Invalid Range')

    if show_count:
        query_countries = query_countries + ' ORDER BY count DESC'

    if count_limit > 0:
        query_countries = query_countries + ' LIMIT %s'
        params.append(count_limit)

    with connection.cursor() as cursor:
        cursor.execute(query_countries, params)
        countries = cursor.fetchall()

    return countries


def gen_attack_countries(time_limit=0, count_limit=0, show_count=True):
    """
    Function used to generate array of countries and attacks
    :param time_limit:
    :param count_limit:
    :param show_count:
    :return:
    """
    params = []
    query_countries = 'SELECT DISTINCT \"countryISO\"'
    if show_count:
        query_countries = query_countries + ', COUNT(\"countryISO\") as count'

    query_countries = query_countries + ' FROM \"CollectorAPI_honeypotinfo\"'
    if time_limit > 0:
        date_from = datetime.now() - timedelta(days=time_limit)
        date_from = pytz.utc.localize(date_from)
        params.append(date_from)
        query_countries = query_countries + ' WHERE event_timestamp > %s'

    if show_count:
        query_countries = query_countries + ' GROUP BY \"countryISO\" ORDER BY count DESC'

    if count_limit > 0:
        query_countries = query_countries + ' LIMIT %s'
        params.append(count_limit)

    with connection.cursor() as cursor:
        cursor.execute(query_countries, params)
        countries = cursor.fetchall()

    return countries


def gen_protocols(time_limit=0, count_limit=0, show_count=True):
    """
    Generate protocol statistics
    :param time_limit:
    :param count_limit:
    :param show_count:
    :return:
    """
    params = []
    query_protocols = 'SELECT DISTINCT protocol'
    if show_count:
        query_protocols = query_protocols + ', COUNT(protocol) as count'

    query_protocols = query_protocols + ' FROM \"CollectorAPI_honeypotinfo\"'
    if time_limit > 0:
        date_from = datetime.now() - timedelta(days=time_limit)
        date_from = pytz.utc.localize(date_from)
        params.append(date_from)
        query_protocols = query_protocols + ' WHERE event_timestamp > %s'
    if show_count:
        query_protocols = query_protocols + ' GROUP BY \"protocol\" ORDER BY count DESC'

    if count_limit > 0:
        query_protocols = query_protocols + ' LIMIT %s'
        params.append(count_limit)

    with connection.cursor() as cursor:
        cursor.execute(query_protocols, params)
        protocols = cursor.fetchall()

    return protocols


def gen_agregated_per_server(time_limit=0, iso='any'):
    """
    Get already generated reports for attack types per server from DB and pass it as JSON
    :param time_limit:
    :param iso:
    :return:
    """
    if time_limit == 1 and iso == 'any':
        data = CollectorDB.HoneypotAgregatePerServer.objects.get(data_id="24h_all")
    elif time_limit == 7 and iso == 'any':
        data = CollectorDB.HoneypotAgregatePerServer.objects.get(data_id="7d_all")
    elif time_limit == 30 and iso == 'any':
        data = CollectorDB.HoneypotAgregatePerServer.objects.get(data_id="30d_all")
    elif time_limit == 1 and iso in ['bg', 'BG']:
        data = CollectorDB.HoneypotAgregatePerServer.objects.get(data_id="24h_bg")
    elif time_limit == 7 and iso in ['bg', 'BG']:
        data = CollectorDB.HoneypotAgregatePerServer.objects.get(data_id="7d_bg")
    elif time_limit == 30 and iso in ['bg', 'BG']:
        data = CollectorDB.HoneypotAgregatePerServer.objects.get(data_id="30d_bg")
    else:
        raise Exception('Invalid Range')
    output = json.dumps(data.data, sort_keys=True)
    return output


def gen_type_per_server(time_limit=0, iso='any'):
    """
    Generate attacks per server report and save it in the DB
    :param time_limit:
    :param iso:
    :return:
    """
    params = []
    query_type_per_server = 'SELECT DISTINCT i.type, i.server_id_id, s.name, s.ip, COUNT(i.type) as count'
    query_type_per_server = query_type_per_server + ' FROM "CollectorAPI_honeypotinfo" as i LEFT JOIN'
    query_type_per_server = query_type_per_server + ' "CollectorAPI_honeypotserver" as s ON (s.id = i.server_id_id)'
    if time_limit > 0 or iso != 'any':
        query_type_per_server = query_type_per_server + ' WHERE'
    if time_limit > 0:
        date_from = datetime.now() - timedelta(days=time_limit)
        date_from = pytz.utc.localize(date_from)
        params.append(date_from)
        query_type_per_server = query_type_per_server + ' event_timestamp > %s'

    if iso != 'any' and iso.isalnum():
        if time_limit > 0:
            query_type_per_server = query_type_per_server + ' AND'
        query_type_per_server = query_type_per_server + ' \"countryISO\" = %s'
        params.append(iso.upper())

    query_type_per_server = query_type_per_server + ' GROUP BY i.type, i.server_id_id, s.name, s.ip'
    query_type_per_server = query_type_per_server + ' ORDER BY i.server_id_id, i.type;'

    with connection.cursor() as cursor:
        cursor.execute(query_type_per_server, params)
        type_per_server = cursor.fetchall()

    output = {}
    tmp_storage = {}
    tmp_storage['01_comult'] = 0
    for tps in type_per_server:
        tmp_storage[tps[0]] = 0

    for tps in type_per_server:
        output[tps[1]] = {'name': tps[2], 'ip': tps[3], 'data': tmp_storage.copy()}

    for tps in type_per_server:
        if output[tps[1]]['data'][tps[0]] == 0:
            output[tps[1]]['data']['01_comult'] += tps[4]
            output[tps[1]]['data'][tps[0]] = tps[4]

    return output


