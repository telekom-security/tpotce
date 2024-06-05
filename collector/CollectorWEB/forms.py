from django import forms
import datetime
import CollectorAPI.models


class GenerateAdvReport(forms.Form):
    honey_request = CollectorAPI.models.HoneyPotServer.objects.all().values_list('id', 'name').order_by('id')
    honeys = []
    for honey in honey_request:
        honeys.append([honey[0], honey[1]])

    countries = [
        ['all', 'all'],
        ['BG', 'България'],
        ['RU', 'Русия']
    ]
    from_date = forms.DateField(widget=forms.SelectDateWidget(years=range(2021, datetime.date.today().year+10)),
                                label='От дата')
    to_date = forms.DateField(widget=forms.SelectDateWidget(years=range(2021, datetime.date.today().year+10)),
                              label='До дата',
                              initial=datetime.datetime.now())
    affected_honeys = forms.MultipleChoiceField(widget=forms.CheckboxSelectMultiple, choices=honeys,
                                                label="Засегнати Хъни потове")
    attacker_countries = forms.MultipleChoiceField(widget=forms.CheckboxSelectMultiple, choices=countries,
                                                   label="Държава на атакуващият")

