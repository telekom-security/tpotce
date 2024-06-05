from django.template.defaultfilters import register

@register.filter
def keyvalue(dict, key):
    return dict[key]