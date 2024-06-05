from django.urls import path

from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('reports', views.reports, name='reports'),
    path('reports_adv', views.reports_adv, name='reports_adv'),
    path('reports/ips_csv', views.get_report_ips_csv, name='report_ips_csv'),
    path('reports/ips_json', views.get_report_ips_json, name='report_ips_json'),
    path('reports/per_server_csv', views.get_report_agregated_per_server_csv, name='report_per_server_csv'),
    path('attack_graph/<str:start_date>/<str:end_date>/<int:min_attacks>/<str:global_ip>/<int:bg_only>', views.attack_graph, name='attack_graph'),
    path('attack_trend/<str:start_date>/<str:end_date>/<str:resolution>/<str:global_ip>/<int:bg_only>', views.attack_trend, name='attack_trend'),
    path('multi_target/<str:ip>', views.multi_target, name='multi_target'),
    path('multi_target_summary/<str:ip>', views.multi_target_summary, name='multi_target_summary'),
    path('graphs', views.graphs, name='graphs'),
    path('tables', views.tables, name='tables'),
    path('table_daily/<str:date>', views.table_daily, name='table_daily'),
    path('table_simple/<str:date>', views.table_simple, name='table_simple'),
    path('table_details/<str:start>/<str:end>/<str:res>', views.table_details, name='table_details')
]
