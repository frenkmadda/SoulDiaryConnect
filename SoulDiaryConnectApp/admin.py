from django.contrib import admin
from .models import Medico, Paziente, NotaDiario, Messaggio

class MedicoAdmin(admin.ModelAdmin):
	search_fields = ['cognome', 'nome', 'codice_identificativo']

class PazienteAdmin(admin.ModelAdmin):
	search_fields = ['cognome', 'nome', 'codice_fiscale']

admin.site.register(Medico, MedicoAdmin)
admin.site.register(Paziente, PazienteAdmin)
admin.site.register(NotaDiario)
admin.site.register(Messaggio)
