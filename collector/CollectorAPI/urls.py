from django.urls import path

from . import views

urlpatterns = [
    path('post', views.post, name='post'),
    path('post_local', views.post_local, name='post_local'),
    path('targets', views.get_targets, name='get_targets'),
    path('time/from', views.get_from_time, name='get_from_time'),
    path('time/to', views.get_to_time, name='get_to_time'),
    path('report/ips', views.get_attack_ips_json, name='get_attack_ips_json'),
    path('report/countries', views.get_attack_countries_json, name='get_attack_ips_json'),
    path('report/protocols', views.get_protocols_json, name='get_attack_ips_json'),
    path('report/type_per_server', views.get_type_per_server_json, name='get_type_per_server_json'),
    # path('sync', views.sync_missing_info, name='sync_missing_info'),
    path('agregate/day/ip', views.agregate_ip_24h, name='agregate_ip_day'),
    path('agregate/week/ip', views.agregate_ip_7d, name='agregate_ip_week'),
    path('agregate/month/ip', views.agregate_ip_30d, name='agregate_ip_month'),
    path('agregate/day/country', views.agregate_country_24h, name='agregate_country_day'),
    path('agregate/week/country', views.agregate_country_7d, name='agregate_country_week'),
    path('agregate/month/country', views.agregate_country_30d, name='agregate_country_month'),
    path('agregate/day/perserver', views.agregate_per_server_24h, name='agregate_per_server_day'),
    path('agregate/week/perserver', views.agregate_per_server_7d, name='agregate_per_server_week'),
    path('agregate/month/perserver', views.agregate_per_server_30d, name='agregate_per_server_month'),
    path('agregate/day/perserver/bg', views.agregate_per_server_bg_24h, name='agregate_per_server_bg_day'),
    path('agregate/week/perserver/bg', views.agregate_per_server_bg_7d, name='agregate_per_server_bg_week'),
    path('agregate/month/perserver/bg', views.agregate_per_server_bg_30d, name='agregate_per_server_bg_month'),

]
