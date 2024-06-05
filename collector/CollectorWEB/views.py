import json

import numpy as np
from django.contrib.auth.decorators import permission_required
from django.shortcuts import render, redirect
from django.conf import settings
from django.http import JsonResponse, HttpResponse, HttpResponseRedirect

from django.db import connection

import CollectorAPI.models
import CollectorAPI.views as api_views
import CollectorAPI.reports

from .forms import GenerateAdvReport


import csv

from ipwhois import IPWhois
import datetime
UNKNOWN_color = '#aaaaaa'
OTHER_color = '#6272fc'
BG_color = '#fc6265'
TARGET_color = '#fcbc62'

CITY_colors = [
    "#ffa6a6",
    "#e605ff",
    "#ffdda6",
    "#05f7ff",
    "#f9ffa6",
    "#ffa305",
    "#acffa6",
    "#ffa6f3",
    "#a6f9ff",
    "#dea6ff",
    "#a6beff",
    "#ffa6b6",
    "#8aff05",
    "#1e05ff",
    "#ff051e"
]




class Echo:
    """An object that implements just the write method of the file-like
    interface.
    """
    def write(self, value):
        """Write the value by returning it, instead of storing in a buffer."""
        return value

# Create your views here.


def convert_date(date_str):
    if len(date_str) == 0 or date_str == "none":
        return "none"
    
    day, month, year = date_str.split('.')
    if len(day) > 2:
        raise Exception('Invalid date')
    if len(month) > 2:
        raise Exception('Invalid date')
    if (len(year)) > 4:
        raise Exception('Invalid date')

    a = f'{year}-{month}-{day}'
    return a


def index(request):
    """
    View for /
    :param request:
    :return:
    """

    if not request.user.is_authenticated:
        return redirect('%s?next=%s' % (settings.LOGIN_URL, request.path))
    query_servers = 'SELECT * FROM \"CollectorAPI_honeypotserver\" ORDER BY last_input DESC;'

    with connection.cursor() as cursor:

        cursor.execute(query_servers)
        servers = cursor.fetchall()

    context = {
        'servers': servers,
    }

    return render(request, 'home.html', context)


def reports(request):
    if not request.user.is_authenticated:
        return redirect('%s?next=%s' % (settings.LOGIN_URL, request.path))

    context = {}
    return render(request, 'report.html', context)


@permission_required('CollectorAPI.adv_reports')
def reports_adv(request):
    if not request.user.is_authenticated:
        return redirect('%s?next=%s' % (settings.LOGIN_URL, request.path))
    if request.method == 'POST':
        # create a form instance and populate it with data from the request:
        form = GenerateAdvReport(request.POST)
        if form.is_valid():
            from_date = form.cleaned_data['from_date']
            to_date = form.cleaned_data['to_date']
            affected_honeys = form.cleaned_data['affected_honeys']
            attacker_countries = form.cleaned_data['attacker_countries']
            output = CollectorAPI.reports.gen_report(from_date, to_date, affected_honeys, attacker_countries)
            output = json.loads(output)
            print(output)
            filename = 'report'
            response = HttpResponse(
                content_type='text/csv',
                headers={'Content-Disposition': 'attachment; filename="' + filename + '.csv"'},
            )

            writer = csv.writer(response)
            for ip in output:
                ip = [ip]
                writer.writerow(ip)
            return response

    else:
        form = GenerateAdvReport()

    if 'r' in request.GET:
        r = int(request.GET['r'])

        try:
            obj = CollectorAPI.models.HoneypotReportsStorage.objects.get(id=r)
        except CollectorAPI.models.HoneypotReportsStorage.DoesNotExist:
            return JsonResponse({'error': f'No such report'})
        output = json.loads(obj.data)
        filename = 'report'
        response = HttpResponse(
            content_type='text/csv',
            headers={'Content-Disposition': 'attachment; filename="' + filename + '.csv"'},
        )

        writer = csv.writer(response)
        for ip in output:
            ip = [ip]
            writer.writerow(ip)
        return response


    honeys = CollectorAPI.models.HoneyPotServer.objects.all()
    honeys_clean = {}
    for honey in honeys:
        honeys_clean[honey.id] = honey.name

    reports = CollectorAPI.models.HoneypotReportsStorage.objects.all().order_by('from_date')
    clean_reports = []
    for report in reports:
        honey_to_servers = []
        afected_shit = json.loads(report.affected_honeys)
        for afected_shit_sub in afected_shit:
            honey_to_servers.append(honeys_clean[int(afected_shit_sub)])
        clean_reports.append({
            'id': report.id,
            'from_date': report.from_date,
            'to_date': report.to_date,
            'affected_honeys': honey_to_servers,
            'countries': json.loads(report.countries)
        })

    return render(request, 'report_adv.html', {'form': form, 'reports': clean_reports, 'servers': honeys_clean})


def get_report_ips_csv(request):
    '''
    Function to export reports as CSV
    :param request:
    :return:
    '''

    if not request.user.is_authenticated:
        return redirect('%s?next=%s' % (settings.LOGIN_URL, request.path))

    days = 0
    limit = 0
    ISO = 'any'
    no_count = True
    country = True
    city = True
    if 'no_count' in request.GET:
        no_count = False
        country = False
        city = False

    if 'days' in request.GET:
        days = int(request.GET['days'])

    if 'limit' in request.GET:
        limit = int(request.GET['limit'])

    if 'iso' in request.GET:
        ISO = request.GET['iso']

    ips = api_views.gen_attack_ips(days, limit, ISO, no_count, country, city)

    filename = 'attackers_for_days_' + str(days) + '_from_' + ISO
    if no_count:
        filename = filename + '_with_stats'
    # Create the HttpResponse object with the appropriate CSV header.
    response = HttpResponse(
        content_type='text/csv',
        headers={'Content-Disposition': 'attachment; filename="' + filename + '.csv"'},
    )

    writer = csv.writer(response)
    for ip in ips:
        if no_count:
            try:
                obj = IPWhois(ip[0])
                res = obj.lookup_whois()
                ip_print = ip + (res["nets"][0]['name'],)
            except Exception as e:
                print(e)
                ip_print = ip + (e,)

            ip = ip_print
        writer.writerow(ip)

    return response


def get_report_ips_json(request):
    if not request.user.is_authenticated:
        return redirect('%s?next=%s' % (settings.LOGIN_URL, request.path))

    days = 0
    limit = 0
    ISO = 'any'
    no_count = True
    if 'no_count' in request.GET:
        no_count = False

    if 'days' in request.GET:
        days = int(request.GET['days'])

    if 'limit' in request.GET:
        limit = int(request.GET['limit'])

    if 'iso' in request.GET:
        ISO = request.GET['iso']

    ips = api_views.gen_attack_ips(days, limit, ISO, no_count)
    filename = 'attackers_for_days_' + str(days) + '_from_' + ISO
    if no_count:
        filename = filename + '_with_stats'
    # Create the HttpResponse object with the appropriate CSV header.
    response = HttpResponse(json.dumps(ips), content_type="application/json")
    response['Content-Disposition'] = 'attachment; filename=' + filename + '.json'

    return response


def get_report_agregated_per_server_csv(request):

    if not request.user.is_authenticated:
        return redirect('%s?next=%s' % (settings.LOGIN_URL, request.path))

    filter = '24h_all'
    if 'filter' in request.GET:
        filter = request.GET['filter']

    target_targets = ['24h_all', '7d_all', '30d_all', '24h_bg', '7d_bg', '30d_bg']

    if filter not in target_targets:
        return JsonResponse({'error': f'I see you try to be a hacker!!!'})

    data = CollectorAPI.models.HoneypotAgregatePerServer.objects.get(data_id=filter)
    servers = CollectorAPI.models.HoneyPotServer.objects.all()

    servers_output = []
    json_data = json.dumps(data.data)
    data_array = json.loads(json_data)
    build_top = 0
    print(data_array)
    for server in servers:
        if str(server.id) not in data_array:
            continue
        # check if we have top row with info
        if build_top < 1:
            json_int = json.dumps(data_array[str(server.id)]['data'])
            data_int = json.loads(json_int)
            top_row = ['name', 'ip']
            for id, count in data_int.items():
                if id == '01_comult':
                    top_row.append('Total')
                else:
                    top_row.append(id)
            build_top = 1
            servers_output.append(top_row)

        row = [server.name, server.ip]
        json_int = json.dumps(data_array[str(server.id)]['data'])
        data_int = json.loads(json_int)
        for id, count in data_int.items():
            row.append(count)
        servers_output.append(row)

    filename = 'per_server_' + str(filter)
    response = HttpResponse(
        content_type='text/csv',
        headers={'Content-Disposition': 'attachment; filename="' + filename + '.csv"'},
    )
    writer = csv.writer(response)
    for server in servers_output:
        writer.writerow(server)
    return response


@permission_required('CollectorAPI.graphs')
def graphs(request):

    if not request.user.is_authenticated:
        return redirect('%s?next=%s' % (settings.LOGIN_URL, request.path))

    return render(request, 'graphs.html')


@permission_required('CollectorAPI.graphs')
def attack_graph(request, start_date="none", end_date="none", min_attacks=0, global_ip="none", bg_only=0):

    if not request.user.is_authenticated:
        return redirect('%s?next=%s' % (settings.LOGIN_URL, request.path))

    bg_only = not not bg_only

    try:
        start_date = convert_date(start_date)
    except Exception as e:
        return JsonResponse({'error': f'Issue with start_date {e}'})
    try:
        end_date = convert_date(end_date)
    except Exception as e:
        return JsonResponse({'error': f'Issue with end_date {e}'})

    query = None
    if bg_only:
        query = """
select
  src_ip,
  servers.name,
  count(*) "value",
  city_name
from "CollectorAPI_honeypotinfo" as info
"""
    else:
        query = """
select 
  src_ip,
  servers.name,
  count(*) "value",
  "countryISO" = 'BG'
from "CollectorAPI_honeypotinfo" as info"""

    query += """
inner join "CollectorAPI_honeypotserver" as servers on info.server_id_id = servers.id
"""

    conditions = []
    arguments = []
    if global_ip != "none":
        conditions.append("src_ip = %s")
        arguments.append(global_ip)

    if start_date != "none":
        conditions.append("event_timestamp >= %s")
        arguments.append(start_date)

    if end_date != "none":
        conditions.append("event_timestamp <= %s")
        arguments.append(end_date)

    if bg_only:
        conditions.append('"countryISO" = %s')
        arguments.append("BG")

    if len(conditions) != 0:
        query += """
where
""" + " and ".join(conditions)

    if bg_only:
        query += """
group by
  src_ip,
  servers.name,
  city_name
"""
    else:
        query += """
group by
  src_ip,
  servers.name,
  "countryISO" = 'BG'
"""

    if min_attacks != 0:
        query += " having count('*') > %s"
        arguments.append(min_attacks)

    query += """
order by value desc
"""
    print(query)
    with connection.cursor() as cursor:
        cursor.execute(query, arguments)
        rows = cursor.fetchall()

    def loc_name(loc):
        if loc is None:
            return 'Unknown'

        if type(loc) is bool:
            if loc:
                return "Bulgaria"
            else:
                return "Foreign"
        return loc

    links = []
    rows = [[r[0] + f' ({loc_name(r[3])})', r[1], r[2], r[3]]
            for r in rows]
    for r in rows:
        links.append({'source': r[0], 'target': r[1], 'value': r[2]})
    ips = set()
    ip2color = {}

    if bg_only:
        city2color = {}
        available_colors = CITY_colors.copy()

        for r in rows:
            city = r[3] if r[3] is not None else "Unknown"
            source = r[0]
            target = r[1]

            ips.add(source)
            ips.add(target)

            if target not in ip2color:
                ip2color[target] = {
                    'color': TARGET_color
                }
            if source not in ip2color:
                if city not in city2color:
                    if city == "Unknown":
                        city2color[city] = UNKNOWN_color
                    if len(available_colors) > 0:
                        city2color[city] = available_colors.pop()
                    else:
                        city2color[city] = BG_color
                ip2color[source] = {
                    'color': city2color[city]
                }
        location2color = city2color
    else:
        for r in rows:
            loc = r[3] if r[3] is not None else "Unknown"

            ips.add(r[1])
            ips.add(r[0])

            if r[1] not in ip2color:
                ip2color[r[1]] = {
                    'color': TARGET_color
                }
            if r[0] not in ip2color:
                if loc == "Unknown":
                    ip2color[r[0]] = {
                        'color': UNKNOWN_color
                    }
                elif r[3]:
                    ip2color[r[0]] = {
                        'color': BG_color
                    }
                else:
                    ip2color[r[0]] = {
                        'color': OTHER_color
                    }

        location2color = {
            'Bulgaria': BG_color,
            'Other': OTHER_color
          }
    vertices = [{'name': v, 'itemStyle': ip2color[v]} for v in ips]
    print(ip2color)
    return JsonResponse({'vertices': vertices,
                         'links': links,
                         'location2color': location2color})


@permission_required('CollectorAPI.graphs')
def attack_trend(request, start_date="none", end_date="none", resolution='day', global_ip="none", bg_only=0):
    if not request.user.is_authenticated:
        return redirect('%s?next=%s' % (settings.LOGIN_URL, request.path))

    try:
        start_date = convert_date(start_date)
    except Exception as e:
        return JsonResponse({'error': f'Issue with start_date {e}'})
    try:
        end_date = convert_date(end_date)
    except Exception as e:
        return JsonResponse({'error': f'Issue with end_date {e}'})

    if resolution not in ['hour', 'day', 'week']:
        return JsonResponse({'error': 'Unknown resolution ' + resolution})

    if resolution == 'hour':
        time_step = 3600.0
    elif resolution == 'day':
        time_step = 3600.0*24
    elif resolution == 'week':
        time_step = 3600.0*24*7
    else:
        return JsonResponse({'error': 'Unknown resolution ' + resolution})

    query = "select count(*) \"value\", date_trunc(%s, event_timestamp) timepoint from \"CollectorAPI_honeypotinfo\""
    conditions = []
    arguments = [resolution]
    if start_date != "none":
        conditions.append(" event_timestamp >= %s ")
        arguments.append(start_date)
    if end_date != "none":
        conditions.append(" event_timestamp <= %s ")
        arguments.append(end_date)

    if global_ip != "none":
        conditions.append(" src_ip = %s")
        arguments.append(global_ip)

    if bg_only:
        conditions.append(" \"countryISO\" = 'BG'")

    if len(conditions) != 0:
        query += " where " + " and ".join(conditions)

    query += " group by timepoint order by timepoint asc"
    print(query)
    with connection.cursor() as cursor:
        cursor.execute(query, arguments)
        rows = cursor.fetchall()

    if len(rows) < 3:
        return JsonResponse({'error': f'Insufficient data for regression analysis for start \
{start_date} end {end_date} and resolution {resolution}'})
    data = np.zeros((len(rows), 2))

    for i, r in enumerate(rows):
        data[i, :] = [(r[1] - rows[0][1]).total_seconds()/time_step, r[0]]

    n = data.shape[0]
    # The growth model is y = a*x + b
    # From this the formula for a will be a = \frac{\sum_i (x_i - \mu_x)(y_i - \mu_i)}{\sum_i (x - \mu_x)^2}
    # Having a calculated, b = \mu_y - a \mu_x
    a = np.sum(np.prod(data - np.mean(data, axis=0)[None], axis=1))/np.sum((data[:, 0] - np.mean(data[:, 0]))**2)
    b = np.mean(data[:, 1]) - a*np.mean(data[:, 0])

    # Since the parameters are calculated, the confidence interval of the slope should be calculated
    # according to \sigma_a = \sqrt{\frac{\sum_i (y_i - a x_i - b)^2}{(n-2)\sum_i (x_i - \mu_x)^2}}
    sigma_a = np.sqrt(np.sum((data[:, 1] - (a * data[:, 0] + b))**2) /
                      ((n - 2) * np.sum((data[:, 0] - np.mean(data[:, 0]))**2))
                      )

    return JsonResponse({'a': a, 'b': b,
                         'data': data.tolist(),
                         'max_x': np.max(data[:, 0]),
                         'first_attack': rows[0][1].strftime("%d %b %Y %H:%m:%S %Z"),
                         'sigma_a': sigma_a})


@permission_required('CollectorAPI.graphs')
def multi_target(request, ip):

    if not request.user.is_authenticated:
        return redirect('%s?next=%s' % (settings.LOGIN_URL, request.path))

    if len(ip) == 0:
        return JsonResponse({'error': 'Invalid IP'})

    query = """select
  min(info.event_timestamp) + (max(info.event_timestamp) - min(info.event_timestamp)) / 2 "x",
  count(*) "y", 
  info.type,
  server.name
from
  "CollectorAPI_honeypotinfo" as info
inner join "CollectorAPI_honeypotserver" as server
  on info.server_id_id = server.id
where
 info.src_ip = %s
group by
  info.type, server.name, date_trunc('hour', info.event_timestamp)
order by
  server.name, info.type, "x" """

    with connection.cursor() as cursor:
        cursor.execute(query, [ip])
        rows = cursor.fetchall()

    result = {}
    attack_start = None
    attack_end = None

    max_attacks = None

    for row in rows:
        if attack_start is None:
            attack_start = row[0]
        else:
            attack_start = min(row[0], attack_start)

        if attack_end is None:
            attack_end = row[0]
        else:
            attack_end = max(row[0], attack_end)

        if max_attacks is None:
            max_attacks = row[1]
        else:
            max_attacks = max(max_attacks, row[1])

        if row[3] not in result:
            result[row[3]] = {}
        if row[2] not in result[row[3]]:
            result[row[3]][row[2]] = []
        result[row[3]][row[2]].append([row[0].strftime("%d %b %Y %H:%m:%S %Z"), row[1]])

    return JsonResponse({'data': result,
                         'attack_start': attack_start.strftime("%d %b %Y %H:%m:%S %Z"),
                         'attack_end': attack_end.strftime("%d %b %Y %H:%m:%S %Z"),
                         'max_attacks': max_attacks})


@permission_required('CollectorAPI.graphs')
def multi_target_summary(request, ip):

    if not request.user.is_authenticated:
        return redirect('%s?next=%s' % (settings.LOGIN_URL, request.path))

    if len(ip) == 0:
        return JsonResponse({'error': 'Invalid IP'})

    query = """
select
  date_trunc('day', event_timestamp) "day",
  server.name "name",
  count(*) "value",
  info.type
from "CollectorAPI_honeypotinfo" as info
inner join "CollectorAPI_honeypotserver" as server
  on info.server_id_id = server.id
where src_ip = %s
group by server.name, day, info.type;
"""
    with connection.cursor() as cursor:
        cursor.execute(query, [ip])
        rows = cursor.fetchall()

    max_attacks = None
    data = {}
    for row in rows:
        if max_attacks is None:
            max_attacks = row[2]
        else:
            max_attacks = max(max_attacks, row[2])

    for row in rows:
        if row[3] not in data:
            data[row[3]] = []
        data[row[3]].append([row[0].strftime("%d %b %Y"), row[1], float(row[2]) * 100.0 / float(max_attacks)])

    return JsonResponse({
        'data': data
     })


@permission_required('CollectorAPI.tables')
def tables(request):
    if not request.user.is_authenticated:
        return redirect('%s?next=%s' % (settings.LOGIN_URL, request.path))

    return render(request, 'tables.html')


@permission_required('CollectorAPI.tables')
def table_daily(request, date):
    psql_date = convert_date(date)
    query_targets = """
select "name", "id" from "CollectorAPI_honeypotserver"
"""

    query_per_ip = """
select count(*) "attacks", src_ip from "CollectorAPI_honeypotinfo"
where
     "countryISO" = 'BG'
and event_timestamp >= %s
and event_timestamp <= (%s::date + interval '1d')
and server_id_id = %s
group by
  src_ip
order by
  attacks desc
"""
    query_bg = """
select count(*) from "CollectorAPI_honeypotinfo"
where
    "countryISO" = 'BG'
and event_timestamp >= %s
and event_timestamp <= (%s::date + interval '1d')
and server_id_id = %s
"""
    query_all = """
select count(*) from "CollectorAPI_honeypotinfo"
where
    event_timestamp >= %s
and event_timestamp <= (%s::date + interval '1d')
and server_id_id = %s
"""

    with connection.cursor() as cursor:
        cursor.execute(query_targets)
        servers = cursor.fetchall()

    table_data = {}
    for name, s_id in servers:
        with connection.cursor() as cursor:
            cursor.execute(query_per_ip, [psql_date, psql_date, s_id])
            top_ips = cursor.fetchall()

        with connection.cursor() as cursor:
            cursor.execute(query_bg, [psql_date, psql_date, s_id])
            bg_attacks = cursor.fetchall()

        with connection.cursor() as cursor:
            cursor.execute(query_all, [psql_date, psql_date, s_id])
            all_attacks = cursor.fetchall()

        if len(top_ips) == 0:
            continue

        table_data[name] = {
            'rest_top_ips': top_ips[1:],
            'bg_attacks': bg_attacks[0][0],
            'all_attacks': all_attacks[0][0],
            'top_ip': top_ips[0][1],
            'top_count': top_ips[0][0]
        }
    context = {'table_data': table_data,
               'date': date,
               'has_data': len(table_data) != 0}
    return render(request, 'table_daily.html', context=context)


@permission_required('CollectorAPI.tables')
def table_simple(request, date):

    if not request.user.is_authenticated:
        return redirect('%s?next=%s' % (settings.LOGIN_URL, request.path))

    def process_rows(rows):
        processed = {}
        for row in rows:
            if row[1] not in processed:
                processed[row[1]] = {
                    'domestic': 0,
                    'total': 0
                }
            if row[2]:
                processed[row[1]]['domestic'] = row[0]
            processed[row[1]]['total'] += row[0]
        return processed

    psql_date = convert_date(date)

    query24h = """
select count(*) "attacks", server.name, info."countryISO" = 'BG' as domestic from "CollectorAPI_honeypotinfo" as info
inner join "CollectorAPI_honeypotserver" as server
  on info.server_id_id = server.id
where
    event_timestamp <= (%s::date + interval '1d')
and event_timestamp >= %s::date
group by
  server.name,
  domestic
order by
  server.name,
  domestic,
  attacks desc
"""

    query7d = """
select count(*) "attacks", server.name, info."countryISO" = 'BG' as domestic from "CollectorAPI_honeypotinfo" as info
inner join "CollectorAPI_honeypotserver" as server
  on info.server_id_id = server.id
where
    event_timestamp <= (%s::date + interval '1d')
and event_timestamp >= (%s::date - interval '6d')
group by
  server.name,
  domestic
order by
  server.name,
  domestic,
  attacks desc
    """

    query30d = """
select count(*) "attacks", server.name, info."countryISO" = 'BG' as domestic from "CollectorAPI_honeypotinfo" as info
inner join "CollectorAPI_honeypotserver" as server
  on info.server_id_id = server.id
where
    event_timestamp <= (%s::date + interval '1d')
and event_timestamp >= (%s::date - interval '29d')
group by
  server.name,
  domestic
order by
  server.name,
  domestic,
  attacks desc
        """

    query24h_date = """
select %s::date, (%s::date + interval '1d');
"""
    query7d_date = """
select (%s::date - interval '6d'), (%s::date + interval '1d');
"""
    query30d_date = """
select (%s::date - interval '29d'), (%s::date + interval '1d');
"""

    with connection.cursor() as cursor:
        cursor.execute(query24h, [psql_date, psql_date])
        summary24h = process_rows(cursor.fetchall())

    with connection.cursor() as cursor:
        cursor.execute(query7d, [psql_date, psql_date])
        summary7d = process_rows(cursor.fetchall())

    with connection.cursor() as cursor:
        cursor.execute(query30d, [psql_date, psql_date])
        summary30d = process_rows(cursor.fetchall())

    with connection.cursor() as cursor:
        cursor.execute(query24h_date, [psql_date, psql_date])
        rows = cursor.fetchall()
    date24h = {
        'start': rows[0][0].strftime("%d %b %Y"),
        'end': rows[0][1].strftime("%d %b %Y")
    }

    with connection.cursor() as cursor:
        cursor.execute(query7d_date, [psql_date, psql_date])
        rows = cursor.fetchall()
    date7d = {
        'start': rows[0][0].strftime("%d %b %Y"),
        'end': rows[0][1].strftime("%d %b %Y")
    }

    with connection.cursor() as cursor:
        cursor.execute(query30d_date, [psql_date, psql_date])
        rows = cursor.fetchall()
    date30d = {
        'start': rows[0][0].strftime("%d %b %Y"),
        'end': rows[0][1].strftime("%d %b %Y")
    }

    total = {
        'summary24h': {
            'domestic': 0,
            'total': 0
        },
        'summary7d': {
            'domestic': 0,
            'total': 0
        },
        'summary30d': {
            'domestic': 0,
            'total': 0
        }
    }
    all_targets = set(
        list(summary24h.keys()) +
        list(summary7d.keys()) +
        list(summary30d.keys())
    )

    for k in summary24h:
        all_targets.add(k)

    for k in summary24h:
        total['summary24h']['domestic'] += summary24h[k]['domestic']
        total['summary24h']['total'] += summary24h[k]['total']

    for k in summary7d:
        total['summary7d']['domestic'] += summary7d[k]['domestic']
        total['summary7d']['total'] += summary7d[k]['total']

    for k in summary30d:
        total['summary30d']['domestic'] += summary30d[k]['domestic']
        total['summary30d']['total'] += summary30d[k]['total']

    context = {'summary24h': summary24h,
               'summary7d': summary7d,
               'summary30d': summary30d,
               'total': total,
               'all_targets': list(all_targets),
               'date24h': date24h,
               'date7d': date7d,
               'date30d': date30d}

    return render(request, "table_simple.html", context=context)


@permission_required('CollectorAPI.tables')
def table_details(request, start, end, res):

    if not request.user.is_authenticated:
        return redirect('%s?next=%s' % (settings.LOGIN_URL, request.path))

    psql_start = convert_date(start)
    psql_end = convert_date(end)

    query = """
select
  count(*) "attacks",
  count(distinct(info.src_ip)) "ips",
  info."countryISO" = 'BG' "domestic",
  server.name,
  date_trunc(%s, info.event_timestamp)  "event_date"
from "CollectorAPI_honeypotinfo" as info
inner join "CollectorAPI_honeypotserver" as server
  on info.server_id_id = server.id
where
    info.event_timestamp >= %s
and info.event_timestamp <= %s
group by
  domestic,
  server.name,
  event_date
"""
    with connection.cursor() as cursor:
        cursor.execute(query, [res, psql_start, psql_end])
        rows = cursor.fetchall()

    time_points = set()
    targets = set()

    data = {}
    total_over_time = {}
    total_over_targets = {}
    total = {
        'bg': {
            'attacks': 0,
            'ips': 0
        },
        'all': {
            'attacks': 0,
            'ips': 0
        }
    }
    for row in rows:
        time_point = row[4].strftime("%d %b %Y")
        server = row[3]
        domestic = not not row[2]
        ips = row[1]
        attacks = row[0]

        time_points.add(row[4])
        targets.add(server)

        if time_point not in data:
            data[time_point] = {}
        if server not in data[time_point]:
            data[time_point][server] = {
                'bg': {
                    'attacks': 0,
                    'ips': 0
                },
                'all': {
                    'attacks': 0,
                    'ips': 0
                }
            }
        if domestic:
            data[time_point][server]['bg']['attacks'] = attacks
            data[time_point][server]['bg']['ips'] = ips
        data[time_point][server]['all']['attacks'] += attacks
        data[time_point][server]['all']['ips'] += ips

        if time_point not in total_over_targets:
            total_over_targets[time_point] = {
                'bg': {
                    'attacks': 0,
                    'ips': 0
                },
                'all': {
                    'attacks': 0,
                    'ips': 0
                }
            }
        if domestic:
            total_over_targets[time_point]['bg']['attacks'] += attacks
            total_over_targets[time_point]['bg']['ips'] += ips
        total_over_targets[time_point]['all']['attacks'] += attacks
        total_over_targets[time_point]['all']['ips'] += ips

        if server not in total_over_time:
            total_over_time[server] = {
                'bg': {
                    'attacks': 0,
                    'ips': 0
                },
                'all': {
                    'attacks': 0,
                    'ips': 0
                }
            }
        if domestic:
            total_over_time[server]['bg']['attacks'] += attacks
            total_over_time[server]['bg']['ips'] += ips
        total_over_time[server]['all']['attacks'] += attacks
        total_over_time[server]['all']['ips'] += ips
        if domestic:
            total['bg']['attacks'] += attacks
            total['bg']['ips'] += ips
        total['all']['attacks'] += attacks
        total['all']['ips'] += ips

    context = {
        'time_points': [d.strftime("%d %b %Y") for d in sorted(list(time_points))],
        'targets': sorted(list(targets)),
        'data': data,
        'total_over_time': total_over_time,
        'total_over_targets': total_over_targets,
        'total': total
    }

    return render(request, "table_details.html", context=context)
